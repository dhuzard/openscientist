"""Versioned, resumable workflow state for governed DVC analyses (DVC-203)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DVCWorkflowError(RuntimeError):
    """Base error for workflow persistence and transition failures."""


class DVCWorkflowConflictError(DVCWorkflowError):
    """A retry conflicts with persisted workflow state."""


class DVCWorkflowCorruptError(DVCWorkflowError):
    """The persisted workflow record cannot be trusted."""


class DVCWorkflowStage(StrEnum):
    INITIALIZED = "initialized"
    ACQUIRED = "acquired"
    PRE_ASSESSED = "pre_assessed"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    ANALYZED = "analyzed"
    POST_ASSESSED = "post_assessed"
    REPORTED = "reported"


# A job may contain more than one bounded DVC dataset. ACQUIRED is therefore an
# explicit restart point after a completed or partially completed dataset flow.
_ALLOWED_TRANSITIONS: dict[DVCWorkflowStage, frozenset[DVCWorkflowStage]] = {
    DVCWorkflowStage.INITIALIZED: frozenset(
        {DVCWorkflowStage.INITIALIZED, DVCWorkflowStage.ACQUIRED}
    ),
    DVCWorkflowStage.ACQUIRED: frozenset(
        {DVCWorkflowStage.ACQUIRED, DVCWorkflowStage.PRE_ASSESSED}
    ),
    DVCWorkflowStage.PRE_ASSESSED: frozenset(
        {
            DVCWorkflowStage.ACQUIRED,
            DVCWorkflowStage.PRE_ASSESSED,
            DVCWorkflowStage.PENDING_APPROVAL,
            DVCWorkflowStage.ANALYZED,
        }
    ),
    DVCWorkflowStage.PENDING_APPROVAL: frozenset(
        {
            DVCWorkflowStage.ACQUIRED,
            DVCWorkflowStage.PRE_ASSESSED,
            DVCWorkflowStage.PENDING_APPROVAL,
            DVCWorkflowStage.APPROVED,
        }
    ),
    DVCWorkflowStage.APPROVED: frozenset(
        {
            DVCWorkflowStage.ACQUIRED,
            DVCWorkflowStage.PRE_ASSESSED,
            DVCWorkflowStage.APPROVED,
            DVCWorkflowStage.ANALYZED,
        }
    ),
    DVCWorkflowStage.ANALYZED: frozenset(
        {
            DVCWorkflowStage.ACQUIRED,
            DVCWorkflowStage.PRE_ASSESSED,
            DVCWorkflowStage.ANALYZED,
            DVCWorkflowStage.POST_ASSESSED,
        }
    ),
    DVCWorkflowStage.POST_ASSESSED: frozenset(
        {
            DVCWorkflowStage.ACQUIRED,
            DVCWorkflowStage.PRE_ASSESSED,
            DVCWorkflowStage.POST_ASSESSED,
            DVCWorkflowStage.REPORTED,
        }
    ),
    DVCWorkflowStage.REPORTED: frozenset({DVCWorkflowStage.ACQUIRED, DVCWorkflowStage.REPORTED}),
}


class DVCWorkflowTransition(StrictModel):
    transition_id: str = Field(default_factory=lambda: f"dvc-transition-{uuid4()}")
    from_stage: DVCWorkflowStage
    to_stage: DVCWorkflowStage
    actor: str = Field(min_length=1, max_length=200)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    previous_version: int = Field(ge=0)
    version: int = Field(ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=300)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Workflow transition timestamp must include a timezone.")
        return value


class DVCWorkflowFailure(StrictModel):
    failure_id: str = Field(default_factory=lambda: f"dvc-failure-{uuid4()}")
    operation: str = Field(min_length=1, max_length=200)
    actor: str = Field(min_length=1, max_length=200)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error_type: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("occurred_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Workflow failure timestamp must include a timezone.")
        return value


class DVCWorkflowState(StrictModel):
    schema_version: Literal["openscientist-dvc-workflow-state/0.2"] = (
        "openscientist-dvc-workflow-state/0.2"
    )
    job_id: str = Field(min_length=1)
    version: int = Field(default=0, ge=0)
    current_stage: DVCWorkflowStage = DVCWorkflowStage.INITIALIZED
    context_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    transitions: list[DVCWorkflowTransition] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    checkpoints: list[str] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)
    executions: list[str] = Field(default_factory=list)
    reports: list[str] = Field(default_factory=list)
    failures: list[DVCWorkflowFailure] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_history(self) -> "DVCWorkflowState":
        expected_stage = DVCWorkflowStage.INITIALIZED
        idempotency_keys: set[str] = set()
        for expected_version, transition in enumerate(self.transitions, start=1):
            if transition.previous_version != expected_version - 1:
                raise ValueError("Workflow transition previous_version is not contiguous.")
            if transition.version != expected_version:
                raise ValueError("Workflow transition version is not contiguous.")
            if transition.from_stage != expected_stage:
                raise ValueError("Workflow transition history has a broken stage chain.")
            if transition.to_stage not in _ALLOWED_TRANSITIONS[expected_stage]:
                raise ValueError("Workflow transition history contains an invalid transition.")
            expected_payload_sha256 = _payload_sha256(
                to_stage=transition.to_stage,
                actor=transition.actor,
                details=transition.details,
            )
            if transition.payload_sha256 != expected_payload_sha256:
                raise ValueError("Workflow transition payload hash does not match its content.")
            if transition.idempotency_key:
                if transition.idempotency_key in idempotency_keys:
                    raise ValueError("Workflow idempotency keys must be unique.")
                idempotency_keys.add(transition.idempotency_key)
            expected_stage = transition.to_stage
        if self.version != len(self.transitions):
            raise ValueError("Workflow state version does not match its transition history.")
        if self.current_stage != expected_stage:
            raise ValueError("Workflow current stage does not match its transition history.")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("Workflow update timestamp must include a timezone.")
        return self


def _payload_sha256(
    *,
    to_stage: DVCWorkflowStage,
    actor: str,
    details: dict[str, Any],
) -> str:
    try:
        payload = json.dumps(
            {"actor": actor, "details": details, "to_stage": to_stage.value},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Workflow transition details must be canonical JSON values.") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DVCWorkflowStore:
    """Atomic, process-thread-safe store with optimistic version checks."""

    _locks_guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}

    def __init__(self, job_dir: Path, job_id: str | None = None) -> None:
        self.job_dir = Path(job_dir)
        self.job_id = job_id or self.job_dir.name
        self.state_file = self.job_dir / "dvc_workflow.json"
        self.lock_file = self.job_dir / ".dvc_workflow.lock"
        self._file_lock = FileLock(self.lock_file, timeout=10)
        lock_key = str(self.state_file.resolve())
        with self._locks_guard:
            self._lock = self._locks.setdefault(lock_key, threading.RLock())

    @contextmanager
    def _coordinated(self) -> Iterator[None]:
        self.job_dir.mkdir(parents=True, exist_ok=True)
        try:
            with self._lock, self._file_lock:
                yield
        except Timeout as exc:
            raise DVCWorkflowConflictError(
                "Timed out waiting for another DVC workflow writer."
            ) from exc

    def load(self) -> DVCWorkflowState:
        with self._coordinated():
            if not self.state_file.is_file():
                return DVCWorkflowState(job_id=self.job_id)
            try:
                payload = json.loads(self.state_file.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise TypeError("workflow root must be a JSON object")
                payload = self._migrate(payload)
                state = DVCWorkflowState.model_validate(payload)
            except (
                KeyError,
                OSError,
                UnicodeError,
                ValueError,
                TypeError,
            ) as exc:
                raise DVCWorkflowCorruptError(
                    f"DVC workflow state is invalid: {self.state_file}"
                ) from exc
            if state.job_id != self.job_id:
                raise DVCWorkflowCorruptError(
                    "DVC workflow job identity does not match its directory."
                )
            return state

    @staticmethod
    def _migrate(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("schema_version") != "openscientist-dvc-workflow-state/0.1":
            return payload
        migrated = dict(payload)
        migrated["schema_version"] = "openscientist-dvc-workflow-state/0.2"
        transitions = []
        for index, raw_transition in enumerate(migrated.get("transitions", []), start=1):
            transition = dict(raw_transition)
            transition["previous_version"] = index - 1
            transition["version"] = index
            transition["payload_sha256"] = _payload_sha256(
                to_stage=DVCWorkflowStage(transition["to_stage"]),
                actor=str(transition["actor"]),
                details=dict(transition.get("details", {})),
            )
            transitions.append(transition)
        migrated["transitions"] = transitions
        migrated["version"] = len(transitions)
        migrated.setdefault("context_sha256", None)
        migrated.setdefault("reports", [])
        migrated.setdefault("failures", [])
        return migrated

    def save(self, state: DVCWorkflowState) -> None:
        with self._coordinated():
            if state.job_id != self.job_id:
                raise DVCWorkflowConflictError("Cannot save workflow state for a different job.")
            state.updated_at = datetime.now(timezone.utc)
            state = DVCWorkflowState.model_validate(state.model_dump(mode="python"))
            self.job_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.state_file.with_name(f".{self.state_file.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_text(
                    json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                os.replace(temporary, self.state_file)
            finally:
                temporary.unlink(missing_ok=True)

    def transition(
        self,
        to_stage: DVCWorkflowStage,
        actor: str,
        *,
        idempotency_key: str | None = None,
        details: dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> DVCWorkflowState:
        with self._coordinated():
            state = self.load()
            return self._transition_state(
                state,
                to_stage,
                actor,
                idempotency_key=idempotency_key,
                details=details,
                expected_version=expected_version,
            )

    def _transition_state(
        self,
        state: DVCWorkflowState,
        to_stage: DVCWorkflowStage,
        actor: str,
        *,
        idempotency_key: str | None,
        details: dict[str, Any] | None,
        expected_version: int | None = None,
    ) -> DVCWorkflowState:
        transition_details = details or {}
        fingerprint = _payload_sha256(
            to_stage=to_stage,
            actor=actor,
            details=transition_details,
        )
        if idempotency_key:
            previous = next(
                (item for item in state.transitions if item.idempotency_key == idempotency_key),
                None,
            )
            if previous is not None:
                if previous.payload_sha256 != fingerprint:
                    raise DVCWorkflowConflictError(
                        f"Idempotency key '{idempotency_key}' was already used for a different "
                        "workflow transition."
                    )
                return state
        if expected_version is not None and state.version != expected_version:
            raise DVCWorkflowConflictError(
                f"Workflow version conflict: expected {expected_version}, found {state.version}."
            )
        if to_stage not in _ALLOWED_TRANSITIONS[state.current_stage]:
            raise DVCWorkflowConflictError(
                f"Invalid DVC workflow transition: {state.current_stage.value} -> {to_stage.value}."
            )

        next_version = state.version + 1
        state.transitions.append(
            DVCWorkflowTransition(
                from_stage=state.current_stage,
                to_stage=to_stage,
                actor=actor,
                previous_version=state.version,
                version=next_version,
                idempotency_key=idempotency_key,
                payload_sha256=fingerprint,
                details=transition_details,
            )
        )
        state.current_stage = to_stage
        state.version = next_version
        self.save(state)
        return state

    def record_dataset(self, dataset_id: str, actor: str = "dvc_gateway") -> DVCWorkflowState:
        with self._coordinated():
            state = self.load()
            if dataset_id not in state.datasets:
                state.datasets.append(dataset_id)
            return self._transition_state(
                state,
                DVCWorkflowStage.ACQUIRED,
                actor,
                idempotency_key=f"acquire:{dataset_id}",
                details={"dataset_id": dataset_id},
            )

    def record_checkpoint(
        self,
        checkpoint_id: str,
        *,
        is_pre: bool,
        context_sha256: str | None = None,
        actor: str = "assessment_service",
    ) -> DVCWorkflowState:
        with self._coordinated():
            state = self.load()
            if checkpoint_id not in state.checkpoints:
                state.checkpoints.append(checkpoint_id)
            if is_pre and context_sha256 is not None:
                state.context_sha256 = context_sha256
            target = DVCWorkflowStage.PRE_ASSESSED if is_pre else DVCWorkflowStage.POST_ASSESSED
            return self._transition_state(
                state,
                target,
                actor,
                idempotency_key=f"checkpoint:{checkpoint_id}",
                details={
                    "checkpoint_id": checkpoint_id,
                    "context_sha256": context_sha256,
                    "is_pre": is_pre,
                },
            )

    def record_approval(
        self,
        approval_id: str,
        *,
        checkpoint_id: str,
        dataset_id: str,
        actor: str = "authenticated_user",
    ) -> DVCWorkflowState:
        with self._coordinated():
            state = self.load()
            if state.current_stage == DVCWorkflowStage.PRE_ASSESSED:
                state = self._transition_state(
                    state,
                    DVCWorkflowStage.PENDING_APPROVAL,
                    actor,
                    idempotency_key=f"pending-approval:{checkpoint_id}",
                    details={"checkpoint_id": checkpoint_id, "dataset_id": dataset_id},
                )
            if approval_id not in state.approvals:
                state.approvals.append(approval_id)
            return self._transition_state(
                state,
                DVCWorkflowStage.APPROVED,
                actor,
                idempotency_key=f"approval:{approval_id}",
                details={
                    "approval_id": approval_id,
                    "checkpoint_id": checkpoint_id,
                    "dataset_id": dataset_id,
                },
            )

    def record_execution(
        self,
        execution_id: str,
        *,
        dataset_id: str,
        operation: str,
        actor: str = "analysis_service",
    ) -> DVCWorkflowState:
        with self._coordinated():
            state = self.load()
            if execution_id not in state.executions:
                state.executions.append(execution_id)
            return self._transition_state(
                state,
                DVCWorkflowStage.ANALYZED,
                actor,
                idempotency_key=f"execution:{execution_id}",
                details={
                    "dataset_id": dataset_id,
                    "execution_id": execution_id,
                    "operation": operation,
                },
            )

    def record_report(
        self,
        report_id: str,
        *,
        actor: str = "report_service",
    ) -> DVCWorkflowState:
        with self._coordinated():
            state = self.load()
            if report_id not in state.reports:
                state.reports.append(report_id)
            return self._transition_state(
                state,
                DVCWorkflowStage.REPORTED,
                actor,
                idempotency_key=f"report:{report_id}",
                details={"report_id": report_id},
            )

    def record_failure(
        self,
        operation: str,
        error: Exception,
        *,
        idempotency_key: str,
        actor: str,
    ) -> DVCWorkflowState:
        with self._coordinated():
            state = self.load()
            failure_key_sha256 = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
            failure = DVCWorkflowFailure(
                failure_id=f"dvc-failure-{failure_key_sha256[:32]}",
                operation=operation,
                actor=actor,
                error_type=type(error).__name__,
                message=(str(error) or type(error).__name__)[:2000],
            )
            if not any(item.failure_id == failure.failure_id for item in state.failures):
                state.failures.append(failure)
            return self._transition_state(
                state,
                state.current_stage,
                actor,
                idempotency_key=idempotency_key,
                details={
                    "error_type": failure.error_type,
                    "failure_id": failure.failure_id,
                    "operation": operation,
                },
            )
