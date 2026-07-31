"""Pre- and post-analysis FAIR/PREPARE assessment checkpoints."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from openscientist.integrations.dvc.execution import (
    DVCAnalysisBlockedError,
    canonical_context_sha256,
)
from openscientist.integrations.fair_prepare import FairPrepareProvider, HttpFairPrepareProvider
from openscientist.preclinical_context.models import AssessmentResult, PreclinicalStudyContext


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DVCCheckpointResult(StrictModel):
    checkpoint_id: str
    checkpoint: Literal["pre_analysis", "post_analysis"]
    dataset_id: str
    context_sha256: str | None = None
    created_at: str
    assessments: list[AssessmentResult]
    relative_path: str


class DVCAssessmentService:
    def __init__(
        self,
        job_dir: Path,
        provider: FairPrepareProvider | None = None,
    ) -> None:
        self.job_dir = Path(job_dir)
        self.provider = provider or HttpFairPrepareProvider()

    def _dataset_dir(self, dataset_id: str) -> Path:
        if not re.fullmatch(r"dvc-[0-9a-fA-F-]{36}", dataset_id):
            raise ValueError("Invalid DVC dataset id.")
        root = (self.job_dir / "dvc_datasets").resolve()
        path = (root / dataset_id).resolve()
        if root not in path.parents or not path.is_dir():
            raise FileNotFoundError(f"DVC dataset not found: {dataset_id}")
        return path

    def pre_analysis(
        self,
        dataset_id: str,
        context: PreclinicalStudyContext,
    ) -> DVCCheckpointResult:
        self._dataset_dir(dataset_id)
        assessments = self.provider.assess_context(
            context,
            frameworks=("prepare-v1", "arrive-v2"),
        )
        return self._persist(
            "pre_analysis",
            dataset_id,
            assessments,
            context_sha256=canonical_context_sha256(context),
        )

    def post_analysis(self, dataset_id: str) -> DVCCheckpointResult:
        dataset_dir = self._dataset_dir(dataset_id)
        analysis_root = self.job_dir / "dvc_analyses"
        completed_analyses = self._completed_analyses(dataset_id, analysis_root)
        if not completed_analyses:
            raise DVCAnalysisBlockedError(
                "DVC post-analysis assessment is blocked.",
                blockers=["At least one completed analysis is required before post-analysis."],
            )
        bundle_dir = self.job_dir / "dvc_bundles" / dataset_id
        bundle_dir.mkdir(parents=True, exist_ok=True)
        for name in ("manifest.json", "measurements.csv", "events.csv"):
            source = dataset_dir / name
            if source.is_file():
                (bundle_dir / name).write_bytes(source.read_bytes())
        (bundle_dir / "analysis-index.json").write_text(
            json.dumps(completed_analyses, indent=2, sort_keys=True), encoding="utf-8"
        )
        assessments = self.provider.assess_bundle(
            bundle_dir,
            frameworks=("arrive-v2", "mnms-v1"),
        )
        return self._persist("post_analysis", dataset_id, assessments)

    @staticmethod
    def _completed_analyses(dataset_id: str, analysis_root: Path) -> list[dict]:
        completed = []
        if not analysis_root.is_dir():
            return completed
        for provenance_path in sorted(analysis_root.glob("*/provenance.json")):
            result_path = provenance_path.with_name("result.json")
            if not result_path.is_file():
                continue
            try:
                provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                provenance.get("dataset_id") == dataset_id
                and result.get("dataset_id") == dataset_id
                and result.get("execution_id") == provenance.get("execution_id")
                and result.get("status") == "completed"
            ):
                completed.append(provenance)
        return completed

    def _persist(
        self,
        checkpoint: Literal["pre_analysis", "post_analysis"],
        dataset_id: str,
        assessments: list[AssessmentResult],
        *,
        context_sha256: str | None = None,
    ) -> DVCCheckpointResult:
        checkpoint_id = f"dvc-assess-{uuid4()}"
        target = self.job_dir / "dvc_assessments" / f"{checkpoint_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        result = DVCCheckpointResult(
            checkpoint_id=checkpoint_id,
            checkpoint=checkpoint,
            dataset_id=dataset_id,
            context_sha256=context_sha256,
            created_at=datetime.now(timezone.utc).isoformat(),
            assessments=assessments,
            relative_path=str(target.relative_to(self.job_dir)),
        )
        target.write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return result
