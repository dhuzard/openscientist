"""Compatibility facade over the generic run-scoped assay workflow kernel."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openscientist.assays.workflow import (
    AnalysisRun,
    AnalysisRunConflictError,
    AnalysisRunCorruptError,
    AnalysisRunError,
    AnalysisRunFailure,
    AnalysisRunStage,
    AnalysisRunStore,
    AnalysisRunTransition,
    _payload_sha256,
    _transition_sha256,
)

# Preserve existing DVC public names while behavior comes from the generic kernel.
DVCWorkflowError = AnalysisRunError
DVCWorkflowConflictError = AnalysisRunConflictError
DVCWorkflowCorruptError = AnalysisRunCorruptError
DVCWorkflowStage = AnalysisRunStage
DVCWorkflowTransition = AnalysisRunTransition
DVCWorkflowFailure = AnalysisRunFailure
DVCWorkflowState = AnalysisRun


class DVCWorkflowStore(AnalysisRunStore):
    """Legacy DVC API plus a factory for strict dataset-analysis runs.

    Calls without an analysis identity continue to use ``dvc_workflow.json``.
    Governed execution uses :meth:`for_dvc_analysis`, which stores each immutable
    run under ``assay_runs/<run_id>/run.json``.
    """

    def __init__(self, job_dir: Path, job_id: str | None = None) -> None:
        resolved_job_id = job_id or Path(job_dir).name
        digest = hashlib.sha256(resolved_job_id.encode("utf-8")).hexdigest()[:32]
        super().__init__(
            job_dir,
            run_id=f"dvc-legacy-{digest}",
            study_id="legacy-unbound",
            assay_id="dvc",
            dataset_id="unbound",
            operation_id="legacy",
            job_id=resolved_job_id,
            state_file=Path(job_dir) / "dvc_workflow.json",
            schema_version="openscientist-dvc-workflow-state/0.2",
        )

    @classmethod
    def for_dvc_analysis(
        cls,
        job_dir: Path,
        *,
        study_id: str,
        dataset_id: str,
        operation_id: str,
        context_sha256: str,
        parameters_sha256: str,
        job_id: str | None = None,
    ) -> AnalysisRunStore:
        return AnalysisRunStore.for_analysis(
            job_dir,
            study_id=study_id,
            assay_id="dvc",
            dataset_id=dataset_id,
            operation_id=operation_id,
            context_sha256=context_sha256,
            parameters_sha256=parameters_sha256,
            job_id=job_id,
        )

    def _migrate(self, payload: dict[str, Any]) -> dict[str, Any]:
        schema_version = payload.get("schema_version")
        if schema_version not in {
            "openscientist-dvc-workflow-state/0.1",
            "openscientist-dvc-workflow-state/0.2",
        }:
            return payload
        transitions_payload = payload.get("transitions", [])
        has_generic_identity = all(
            key in payload
            for key in ("run_id", "study_id", "assay_id", "dataset_id", "operation_id")
        )
        has_hash_chain = all(
            isinstance(item, dict)
            and "transition_sha256" in item
            and "previous_transition_sha256" in item
            for item in transitions_payload
        )
        if (
            schema_version == "openscientist-dvc-workflow-state/0.2"
            and has_generic_identity
            and has_hash_chain
        ):
            return payload
        migrated = dict(payload)
        migrated["schema_version"] = "openscientist-dvc-workflow-state/0.2"
        migrated.setdefault("run_id", self.run_id)
        migrated.setdefault("study_id", self.study_id)
        migrated.setdefault("assay_id", "dvc")
        datasets = list(migrated.get("datasets", []))
        migrated.setdefault("dataset_id", datasets[0] if datasets else "unbound")
        migrated.setdefault("operation_id", "legacy")
        migrated.setdefault("parameters_sha256", None)
        migrated.setdefault("context_sha256", None)
        migrated.setdefault("approval_decisions", [])
        migrated.setdefault("evidence", [])
        migrated.setdefault("reports", [])
        migrated.setdefault("failures", [])
        migrated.setdefault("created_at", migrated.get("updated_at", datetime.now(timezone.utc)))

        transitions: list[dict[str, Any]] = []
        previous_hash: str | None = None
        for index, raw_transition in enumerate(migrated.get("transitions", []), start=1):
            transition = dict(raw_transition)
            transition["previous_version"] = index - 1
            transition["version"] = index
            to_stage = AnalysisRunStage(transition["to_stage"])
            from_stage = AnalysisRunStage(transition["from_stage"])
            actor = str(transition["actor"])
            details = dict(transition.get("details", {}))
            timestamp_value = transition["timestamp"]
            timestamp = (
                timestamp_value
                if isinstance(timestamp_value, datetime)
                else datetime.fromisoformat(str(timestamp_value).replace("Z", "+00:00"))
            )
            payload_hash = _payload_sha256(
                to_stage=to_stage,
                actor=actor,
                details=details,
            )
            existing_payload_hash = transition.get("payload_sha256")
            if existing_payload_hash is not None and existing_payload_hash != payload_hash:
                raise ValueError(
                    "Legacy DVC workflow transition payload hash does not match its content."
                )
            transition["payload_sha256"] = payload_hash
            transition["previous_transition_sha256"] = previous_hash
            transition["transition_sha256"] = _transition_sha256(
                run_id=str(migrated["run_id"]),
                from_stage=from_stage,
                to_stage=to_stage,
                actor=actor,
                timestamp=timestamp,
                previous_version=index - 1,
                version=index,
                idempotency_key=transition.get("idempotency_key"),
                payload_sha256=payload_hash,
                previous_transition_sha256=previous_hash,
            )
            previous_hash = transition["transition_sha256"]
            transitions.append(transition)
        migrated["transitions"] = transitions
        migrated["version"] = len(transitions)
        return migrated


__all__ = [
    "DVCWorkflowConflictError",
    "DVCWorkflowCorruptError",
    "DVCWorkflowError",
    "DVCWorkflowFailure",
    "DVCWorkflowStage",
    "DVCWorkflowState",
    "DVCWorkflowStore",
    "DVCWorkflowTransition",
]
