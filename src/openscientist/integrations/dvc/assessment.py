"""Pre- and post-analysis FAIR/PREPARE assessment checkpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from openscientist.integrations.fair_prepare import FairPrepareProvider, HttpFairPrepareProvider
from openscientist.preclinical_context.models import AssessmentResult, PreclinicalStudyContext


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DVCCheckpointResult(StrictModel):
    checkpoint_id: str
    checkpoint: Literal["pre_analysis", "post_analysis"]
    dataset_id: str
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

    def pre_analysis(
        self,
        dataset_id: str,
        context: PreclinicalStudyContext,
    ) -> DVCCheckpointResult:
        assessments = self.provider.assess_context(
            context,
            frameworks=("prepare-v1", "arrive-v2"),
        )
        return self._persist("pre_analysis", dataset_id, assessments)

    def post_analysis(self, dataset_id: str) -> DVCCheckpointResult:
        dataset_dir = self.job_dir / "dvc_datasets" / dataset_id
        analysis_root = self.job_dir / "dvc_analyses"
        if not dataset_dir.is_dir():
            raise FileNotFoundError(f"DVC dataset not found: {dataset_id}")
        bundle_dir = self.job_dir / "dvc_bundles" / dataset_id
        bundle_dir.mkdir(parents=True, exist_ok=True)
        for name in ("manifest.json", "measurements.csv", "events.csv"):
            source = dataset_dir / name
            if source.is_file():
                (bundle_dir / name).write_bytes(source.read_bytes())
        if analysis_root.is_dir():
            index = []
            for provenance in sorted(analysis_root.glob("*/provenance.json")):
                payload = json.loads(provenance.read_text(encoding="utf-8"))
                if payload.get("dataset_id") == dataset_id:
                    index.append(payload)
            (bundle_dir / "analysis-index.json").write_text(
                json.dumps(index, indent=2, sort_keys=True), encoding="utf-8"
            )
        assessments = self.provider.assess_bundle(
            bundle_dir,
            frameworks=("arrive-v2", "mnms-v1"),
        )
        return self._persist("post_analysis", dataset_id, assessments)

    def _persist(
        self,
        checkpoint: Literal["pre_analysis", "post_analysis"],
        dataset_id: str,
        assessments: list[AssessmentResult],
    ) -> DVCCheckpointResult:
        checkpoint_id = f"dvc-assess-{uuid4()}"
        target = self.job_dir / "dvc_assessments" / f"{checkpoint_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        result = DVCCheckpointResult(
            checkpoint_id=checkpoint_id,
            checkpoint=checkpoint,
            dataset_id=dataset_id,
            assessments=assessments,
            relative_path=str(target.relative_to(self.job_dir)),
        )
        target.write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return result
