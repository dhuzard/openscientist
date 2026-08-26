"""Run-scoped, hash-chained workflow persistence for governed assays."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openscientist.assays.contracts import ApprovalDecision, EvidenceArtifact


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisRunError(RuntimeError):
    """Base error for analysis-run persistence and transition failures."""


class AnalysisRunConflictError(AnalysisRunError):
    """A retry conflicts with persisted run state."""


class AnalysisRunCorruptError(AnalysisRunError):
    """A persisted run or its transition chain cannot be trusted."""


class AnalysisRunStage(StrEnum):
    INITIALIZED = "initialized"
    ACQUIRED = "acquired"
    PRE_ASSESSED = "pre_assessed"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    ANALYZED = "analyzed"
    POST_ASSESSED = "post_assessed"
    REPORTED = "reported"


STRICT_TRANSITIONS: dict[AnalysisRunStage, frozenset[AnalysisRunStage]] = {
    AnalysisRunStage.INITIALIZED: frozenset(
        {AnalysisRunStage.INITIALIZED, AnalysisRunStage.ACQUIRED}
    ),
    AnalysisRunStage.ACQUIRED: frozenset(
        {AnalysisRunStage.ACQUIRED, AnalysisRunStage.PRE_ASSESSED}
    ),
    AnalysisRunStage.PRE_ASSESSED: frozenset(
        {
            AnalysisRunStage.PRE_ASSESSED,
            AnalysisRunStage.PENDING_APPROVAL,
            AnalysisRunStage.ANALYZED,
        }
    ),
    AnalysisRunStage.PENDING_APPROVAL: frozenset(
        {AnalysisRunStage.PENDING_APPROVAL, AnalysisRunStage.APPROVED}
    ),
    AnalysisRunStage.APPROVED: frozenset({AnalysisRunStage.APPROVED, AnalysisRunStage.ANALYZED}),
    AnalysisRunStage.ANALYZED: frozenset(
        {AnalysisRunStage.ANALYZED, AnalysisRunStage.POST_ASSESSED}
    ),
    AnalysisRunStage.POST_ASSESSED: frozenset(
        {AnalysisRunStage.POST_ASSESSED, AnalysisRunStage.REPORTED}
    ),
    AnalysisRunStage.REPORTED: frozenset({AnalysisRunStage.REPORTED}),
}

# Compatibility workflows may have represented sequential datasets in one file.
LEGACY_DVC_TRANSITIONS: dict[AnalysisRunStage, frozenset[AnalysisRunStage]] = {
    AnalysisRunStage.INITIALIZED: frozenset(
        {AnalysisRunStage.INITIALIZED, AnalysisRunStage.ACQUIRED}
    ),
    AnalysisRunStage.ACQUIRED: frozenset(
        {AnalysisRunStage.ACQUIRED, AnalysisRunStage.PRE_ASSESSED}
    ),
    AnalysisRunStage.PRE_ASSESSED: frozenset(
        {
            AnalysisRunStage.ACQUIRED,
            AnalysisRunStage.PRE_ASSESSED,
            AnalysisRunStage.PENDING_APPROVAL,
            AnalysisRunStage.ANALYZED,
        }
    ),
    AnalysisRunStage.PENDING_APPROVAL: frozenset(
        {
            AnalysisRunStage.ACQUIRED,
            AnalysisRunStage.PRE_ASSESSED,
            AnalysisRunStage.PENDING_APPROVAL,
            AnalysisRunStage.APPROVED,
        }
    ),
    AnalysisRunStage.APPROVED: frozenset(
        {
            AnalysisRunStage.ACQUIRED,
            AnalysisRunStage.PRE_ASSESSED,
            AnalysisRunStage.APPROVED,
            AnalysisRunStage.ANALYZED,
        }
    ),
    AnalysisRunStage.ANALYZED: frozenset(
        {
            AnalysisRunStage.ACQUIRED,
            AnalysisRunStage.PRE_ASSESSED,
            AnalysisRunStage.ANALYZED,
            AnalysisRunStage.POST_ASSESSED,
        }
    ),
    AnalysisRunStage.POST_ASSESSED: frozenset(
        {
            AnalysisRunStage.ACQUIRED,
            AnalysisRunStage.PRE_ASSESSED,
            AnalysisRunStage.POST_ASSESSED,
            AnalysisRunStage.REPORTED,
        }
    ),
    AnalysisRunStage.REPORTED: frozenset({AnalysisRunStage.ACQUIRED, AnalysisRunStage.REPORTED}),
}


def canonical_json_sha256(payload: Mapping[str, Any]) -> str:
    try:
        canonical = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Workflow payloads must contain canonical JSON values.") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_analysis_run_id(
    *,
    study_id: str,
    assay_id: str,
    dataset_id: str,
    operation_id: str,
    context_sha256: str | None,
    parameters_sha256: str | None,
) -> str:
    """Derive an idempotent identity for one dataset plus governed analysis."""

    digest = canonical_json_sha256(
        {
            "assay_id": assay_id,
            "context_sha256": context_sha256,
            "dataset_id": dataset_id,
            "operation_id": operation_id,
            "parameters_sha256": parameters_sha256,
            "study_id": study_id,
        }
    )
    return f"{assay_id}-run-{digest[:32]}"


def _payload_sha256(
    *,
    to_stage: AnalysisRunStage,
    actor: str,
    details: Mapping[str, Any],
) -> str:
    return canonical_json_sha256(
        {"actor": actor, "details": dict(details), "to_stage": to_stage.value}
    )


def _transition_sha256(
    *,
    run_id: str,
    from_stage: AnalysisRunStage,
    to_stage: AnalysisRunStage,
    actor: str,
    timestamp: datetime,
    previous_version: int,
    version: int,
    idempotency_key: str | None,
    payload_sha256: str,
    previous_transition_sha256: str | None,
) -> str:
    return canonical_json_sha256(
        {
            "actor": actor,
            "from_stage": from_stage.value,
            "idempotency_key": idempotency_key,
            "payload_sha256": payload_sha256,
            "previous_transition_sha256": previous_transition_sha256,
            "previous_version": previous_version,
            "run_id": run_id,
            "timestamp": timestamp.isoformat(),
            "to_stage": to_stage.value,
            "version": version,
        }
    )


class AnalysisRunTransition(StrictModel):
    transition_id: str = Field(default_factory=lambda: f"run-transition-{uuid4()}")
    from_stage: AnalysisRunStage
    to_stage: AnalysisRunStage
    actor: str = Field(min_length=1, max_length=200)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    previous_version: int = Field(ge=0)
    version: int = Field(ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=300)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_transition_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    transition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Workflow transition timestamp must include a timezone.")
        return value


class AnalysisRunFailure(StrictModel):
    failure_id: str = Field(default_factory=lambda: f"run-failure-{uuid4()}")
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


class AnalysisRun(StrictModel):
    """Canonical state for one dataset and one governed analysis identity."""

    schema_version: str = "openscientist-analysis-run/1.0"
    run_id: str = Field(min_length=1, max_length=200)
    job_id: str = Field(min_length=1, max_length=200)
    study_id: str = Field(min_length=1, max_length=200)
    assay_id: str = Field(min_length=1, max_length=100)
    dataset_id: str = Field(min_length=1, max_length=200)
    operation_id: str = Field(min_length=1, max_length=100)
    context_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    parameters_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    version: int = Field(default=0, ge=0)
    current_stage: AnalysisRunStage = AnalysisRunStage.INITIALIZED
    transitions: list[AnalysisRunTransition] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    checkpoints: list[str] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)
    approval_decisions: list[ApprovalDecision] = Field(default_factory=list)
    executions: list[str] = Field(default_factory=list)
    reports: list[str] = Field(default_factory=list)
    evidence: list[EvidenceArtifact] = Field(default_factory=list)
    failures: list[AnalysisRunFailure] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("schema_version")
    @classmethod
    def known_schema(cls, value: str) -> str:
        if value not in {
            "openscientist-analysis-run/1.0",
            "openscientist-dvc-workflow-state/0.2",
        }:
            raise ValueError(f"Unsupported analysis-run schema: {value}")
        return value

    @model_validator(mode="after")
    def validate_history(self) -> "AnalysisRun":
        expected_stage = AnalysisRunStage.INITIALIZED
        previous_hash: str | None = None
        idempotency_keys: set[str] = set()
        allowed = (
            LEGACY_DVC_TRANSITIONS
            if self.schema_version == "openscientist-dvc-workflow-state/0.2"
            else STRICT_TRANSITIONS
        )
        for expected_version, transition in enumerate(self.transitions, start=1):
            if transition.previous_version != expected_version - 1:
                raise ValueError("Workflow transition previous_version is not contiguous.")
            if transition.version != expected_version:
                raise ValueError("Workflow transition version is not contiguous.")
            if transition.from_stage != expected_stage:
                raise ValueError("Workflow transition history has a broken stage chain.")
            if transition.to_stage not in allowed[expected_stage]:
                raise ValueError("Workflow transition history contains an invalid transition.")
            payload_hash = _payload_sha256(
                to_stage=transition.to_stage,
                actor=transition.actor,
                details=transition.details,
            )
            if transition.payload_sha256 != payload_hash:
                raise ValueError("Workflow transition payload hash does not match its content.")
            if transition.previous_transition_sha256 != previous_hash:
                raise ValueError("Workflow transition hash chain is broken.")
            transition_hash = _transition_sha256(
                run_id=self.run_id,
                from_stage=transition.from_stage,
                to_stage=transition.to_stage,
                actor=transition.actor,
                timestamp=transition.timestamp,
                previous_version=transition.previous_version,
                version=transition.version,
                idempotency_key=transition.idempotency_key,
                payload_sha256=transition.payload_sha256,
                previous_transition_sha256=transition.previous_transition_sha256,
            )
            if transition.transition_sha256 != transition_hash:
                raise ValueError("Workflow transition hash does not match its content.")
            if transition.idempotency_key:
                if transition.idempotency_key in idempotency_keys:
                    raise ValueError("Workflow idempotency keys must be unique.")
                idempotency_keys.add(transition.idempotency_key)
            expected_stage = transition.to_stage
            previous_hash = transition.transition_sha256
        if self.version != len(self.transitions):
            raise ValueError("Workflow state version does not match its transition history.")
        if self.current_stage != expected_stage:
            raise ValueError("Workflow current stage does not match its transition history.")
        if self.dataset_id not in self.datasets and self.datasets:
            if self.schema_version != "openscientist-dvc-workflow-state/0.2":
                raise ValueError("Analysis run dataset must be present in its dataset ledger.")
        for value in (self.created_at, self.updated_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Workflow timestamps must include a timezone.")
        return self


class AnalysisRunStore:
    """Atomic store with optimistic locking, hash chaining, and idempotent writes."""

    _locks_guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}

    def __init__(
        self,
        job_dir: Path,
        *,
        run_id: str,
        study_id: str,
        assay_id: str,
        dataset_id: str,
        operation_id: str,
        job_id: str | None = None,
        context_sha256: str | None = None,
        parameters_sha256: str | None = None,
        state_file: Path | None = None,
        schema_version: str = "openscientist-analysis-run/1.0",
    ) -> None:
        self.job_dir = Path(job_dir)
        self.run_id = run_id
        self.job_id = job_id or self.job_dir.name
        self.study_id = study_id
        self.assay_id = assay_id
        self.dataset_id = dataset_id
        self.operation_id = operation_id
        self.context_sha256 = context_sha256
        self.parameters_sha256 = parameters_sha256
        self.schema_version = schema_version
        self.state_file = state_file or self.job_dir / "assay_runs" / run_id / "run.json"
        self.lock_file = self.state_file.with_name(".run.lock")
        self._file_lock = FileLock(self.lock_file, timeout=10)
        lock_key = str(self.state_file.resolve())
        with self._locks_guard:
            self._lock = self._locks.setdefault(lock_key, threading.RLock())

    @classmethod
    def for_analysis(
        cls,
        job_dir: Path,
        *,
        study_id: str,
        assay_id: str,
        dataset_id: str,
        operation_id: str,
        context_sha256: str | None,
        parameters_sha256: str | None,
        job_id: str | None = None,
    ) -> "AnalysisRunStore":
        run_id = make_analysis_run_id(
            study_id=study_id,
            assay_id=assay_id,
            dataset_id=dataset_id,
            operation_id=operation_id,
            context_sha256=context_sha256,
            parameters_sha256=parameters_sha256,
        )
        return cls(
            job_dir,
            run_id=run_id,
            study_id=study_id,
            assay_id=assay_id,
            dataset_id=dataset_id,
            operation_id=operation_id,
            job_id=job_id,
            context_sha256=context_sha256,
            parameters_sha256=parameters_sha256,
        )

    @property
    def allowed_transitions(self) -> Mapping[AnalysisRunStage, frozenset[AnalysisRunStage]]:
        return (
            LEGACY_DVC_TRANSITIONS
            if self.schema_version == "openscientist-dvc-workflow-state/0.2"
            else STRICT_TRANSITIONS
        )

    def _new_state(self) -> AnalysisRun:
        datasets = [] if self.dataset_id == "unbound" else [self.dataset_id]
        return AnalysisRun(
            schema_version=self.schema_version,
            run_id=self.run_id,
            job_id=self.job_id,
            study_id=self.study_id,
            assay_id=self.assay_id,
            dataset_id=self.dataset_id,
            operation_id=self.operation_id,
            context_sha256=self.context_sha256,
            parameters_sha256=self.parameters_sha256,
            datasets=datasets,
        )

    @contextmanager
    def _coordinated(self) -> Iterator[None]:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._lock, self._file_lock:
                yield
        except Timeout as exc:
            raise AnalysisRunConflictError(
                "Timed out waiting for another analysis-run writer."
            ) from exc

    def _migrate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    def load(self) -> AnalysisRun:
        with self._coordinated():
            if not self.state_file.is_file():
                return self._new_state()
            try:
                payload = json.loads(self.state_file.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise TypeError("workflow root must be a JSON object")
                state = AnalysisRun.model_validate(self._migrate(payload))
            except (KeyError, OSError, UnicodeError, ValueError, TypeError) as exc:
                raise AnalysisRunCorruptError(
                    f"Analysis-run state is invalid: {self.state_file}"
                ) from exc
            identity = (
                state.run_id,
                state.job_id,
                state.study_id,
                state.assay_id,
                state.operation_id,
            )
            expected = (
                self.run_id,
                self.job_id,
                self.study_id,
                self.assay_id,
                self.operation_id,
            )
            if identity != expected:
                raise AnalysisRunCorruptError(
                    "Analysis-run identity does not match its storage location."
                )
            if self.dataset_id != "unbound" and state.dataset_id != self.dataset_id:
                raise AnalysisRunCorruptError("Analysis-run dataset identity does not match.")
            return state

    def save(self, state: AnalysisRun) -> None:
        with self._coordinated():
            if state.run_id != self.run_id:
                raise AnalysisRunConflictError("Cannot save state for a different analysis run.")
            state.updated_at = datetime.now(timezone.utc)
            state = AnalysisRun.model_validate(state.model_dump(mode="python"))
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
        to_stage: AnalysisRunStage,
        actor: str,
        *,
        idempotency_key: str | None = None,
        details: dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> AnalysisRun:
        with self._coordinated():
            return self._transition_state(
                self.load(),
                to_stage,
                actor,
                idempotency_key=idempotency_key,
                details=details,
                expected_version=expected_version,
            )

    def _transition_state(
        self,
        state: AnalysisRun,
        to_stage: AnalysisRunStage,
        actor: str,
        *,
        idempotency_key: str | None,
        details: dict[str, Any] | None,
        expected_version: int | None = None,
    ) -> AnalysisRun:
        transition_details = details or {}
        payload_hash = _payload_sha256(
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
                if previous.payload_sha256 != payload_hash:
                    raise AnalysisRunConflictError(
                        f"Idempotency key '{idempotency_key}' was already used for a different "
                        "workflow transition."
                    )
                # Ledger fields may have been repaired or enriched before this
                # idempotent transition was recognized (for example, a typed
                # approval decision added to a legacy approval transition).
                self.save(state)
                return state
        if expected_version is not None and state.version != expected_version:
            raise AnalysisRunConflictError(
                f"Workflow version conflict: expected {expected_version}, found {state.version}."
            )
        if to_stage not in self.allowed_transitions[state.current_stage]:
            raise AnalysisRunConflictError(
                f"Invalid analysis-run transition: {state.current_stage.value} -> {to_stage.value}."
            )

        timestamp = datetime.now(timezone.utc)
        next_version = state.version + 1
        previous_hash = state.transitions[-1].transition_sha256 if state.transitions else None
        transition_hash = _transition_sha256(
            run_id=state.run_id,
            from_stage=state.current_stage,
            to_stage=to_stage,
            actor=actor,
            timestamp=timestamp,
            previous_version=state.version,
            version=next_version,
            idempotency_key=idempotency_key,
            payload_sha256=payload_hash,
            previous_transition_sha256=previous_hash,
        )
        state.transitions.append(
            AnalysisRunTransition(
                from_stage=state.current_stage,
                to_stage=to_stage,
                actor=actor,
                timestamp=timestamp,
                previous_version=state.version,
                version=next_version,
                idempotency_key=idempotency_key,
                payload_sha256=payload_hash,
                previous_transition_sha256=previous_hash,
                transition_sha256=transition_hash,
                details=transition_details,
            )
        )
        state.current_stage = to_stage
        state.version = next_version
        self.save(state)
        return state

    def record_dataset(self, dataset_id: str, actor: str = "assay_gateway") -> AnalysisRun:
        with self._coordinated():
            state = self.load()
            if state.dataset_id == "unbound":
                state.dataset_id = dataset_id
                self.dataset_id = dataset_id
            elif state.dataset_id != dataset_id and self.schema_version != (
                "openscientist-dvc-workflow-state/0.2"
            ):
                raise AnalysisRunConflictError("An analysis run cannot change datasets.")
            if dataset_id not in state.datasets:
                state.datasets.append(dataset_id)
            return self._transition_state(
                state,
                AnalysisRunStage.ACQUIRED,
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
    ) -> AnalysisRun:
        with self._coordinated():
            state = self.load()
            if checkpoint_id not in state.checkpoints:
                state.checkpoints.append(checkpoint_id)
            if is_pre and context_sha256 is not None:
                if state.context_sha256 not in (None, context_sha256):
                    raise AnalysisRunConflictError(
                        "Pre-analysis checkpoint context differs from the analysis run."
                    )
                state.context_sha256 = context_sha256
            target = AnalysisRunStage.PRE_ASSESSED if is_pre else AnalysisRunStage.POST_ASSESSED
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
        decision: ApprovalDecision | None = None,
    ) -> AnalysisRun:
        with self._coordinated():
            state = self.load()
            if state.current_stage == AnalysisRunStage.PRE_ASSESSED:
                state = self._transition_state(
                    state,
                    AnalysisRunStage.PENDING_APPROVAL,
                    actor,
                    idempotency_key=f"pending-approval:{checkpoint_id}",
                    details={"checkpoint_id": checkpoint_id, "dataset_id": dataset_id},
                )
            if decision is not None:
                if decision.run_id != state.run_id or decision.approval_id != approval_id:
                    raise AnalysisRunConflictError(
                        "Approval decision identity does not match the analysis run."
                    )
                if decision.decision != "approved":
                    raise AnalysisRunConflictError(
                        f"Cannot approve a run with decision '{decision.decision}'."
                    )
                if not any(item.approval_id == approval_id for item in state.approval_decisions):
                    state.approval_decisions.append(decision)
            if approval_id not in state.approvals:
                state.approvals.append(approval_id)
            return self._transition_state(
                state,
                AnalysisRunStage.APPROVED,
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
    ) -> AnalysisRun:
        if dataset_id != self.dataset_id and self.dataset_id != "unbound":
            raise AnalysisRunConflictError("Execution dataset differs from the analysis run.")
        if operation != self.operation_id and self.operation_id != "legacy":
            raise AnalysisRunConflictError("Execution operation differs from the analysis run.")
        with self._coordinated():
            state = self.load()
            if execution_id not in state.executions:
                state.executions.append(execution_id)
            return self._transition_state(
                state,
                AnalysisRunStage.ANALYZED,
                actor,
                idempotency_key=f"execution:{execution_id}",
                details={
                    "dataset_id": dataset_id,
                    "execution_id": execution_id,
                    "operation": operation,
                },
            )

    def record_report(self, report_id: str, *, actor: str = "report_service") -> AnalysisRun:
        with self._coordinated():
            state = self.load()
            if report_id not in state.reports:
                state.reports.append(report_id)
            return self._transition_state(
                state,
                AnalysisRunStage.REPORTED,
                actor,
                idempotency_key=f"report:{report_id}",
                details={"report_id": report_id},
            )

    def record_evidence(self, artifact: EvidenceArtifact) -> AnalysisRun:
        with self._coordinated():
            state = self.load()
            if artifact.run_id != state.run_id:
                raise AnalysisRunConflictError("Evidence belongs to a different analysis run.")
            previous = next(
                (item for item in state.evidence if item.artifact_id == artifact.artifact_id),
                None,
            )
            comparable_fields = (
                "artifact_id",
                "run_id",
                "assay_id",
                "dataset_id",
                "role",
                "relative_path",
                "sha256",
                "bytes",
                "media_type",
                "schema_id",
            )
            if previous is not None and any(
                getattr(previous, field_name) != getattr(artifact, field_name)
                for field_name in comparable_fields
            ):
                raise AnalysisRunConflictError(
                    f"Evidence artifact '{artifact.artifact_id}' changed after registration."
                )
            if previous is None:
                state.evidence.append(artifact)
            return self._transition_state(
                state,
                state.current_stage,
                "evidence_service",
                idempotency_key=f"evidence:{artifact.artifact_id}",
                details={"artifact_id": artifact.artifact_id, "sha256": artifact.sha256},
            )

    def record_failure(
        self,
        operation: str,
        error: Exception,
        *,
        idempotency_key: str,
        actor: str,
    ) -> AnalysisRun:
        with self._coordinated():
            state = self.load()
            failure_key_sha256 = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
            failure = AnalysisRunFailure(
                failure_id=f"run-failure-{failure_key_sha256[:32]}",
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
