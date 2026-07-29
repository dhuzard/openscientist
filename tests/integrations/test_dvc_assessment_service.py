from __future__ import annotations

import json
from pathlib import Path

import pytest

from openscientist.integrations.dvc.assessment import DVCAssessmentService
from openscientist.integrations.dvc.execution import DVCAnalysisBlockedError
from openscientist.preclinical_context.models import (
    AssessmentFinding,
    AssessmentResult,
    AssessmentStatus,
    PreclinicalStudyContext,
)


class FakeProvider:
    def __init__(self) -> None:
        self.context_frameworks: tuple[str, ...] | None = None
        self.bundle_frameworks: tuple[str, ...] | None = None
        self.bundle_dir: Path | None = None

    def assess_context(self, context, *, frameworks):
        self.context_frameworks = frameworks
        return [
            AssessmentResult(
                assessment_id="pre-1",
                framework="PREPARE",
                framework_version="prepare-v1",
                context_hash="a" * 64,
                findings=[
                    AssessmentFinding(
                        requirement_id="design",
                        status=AssessmentStatus.MISSING,
                        missing_fields=["design.experimental_unit"],
                    )
                ],
            )
        ]

    def assess_bundle(self, bundle_dir, *, frameworks):
        self.bundle_dir = Path(bundle_dir)
        self.bundle_frameworks = frameworks
        return [
            AssessmentResult(
                assessment_id="post-1",
                framework="FAIR",
                framework_version="1.0",
                context_hash="b" * 64,
                findings=[],
            )
        ]


def test_pre_analysis_persists_assessment(tmp_path: Path):
    dataset_id = "dvc-00000000-0000-0000-0000-000000000000"
    (tmp_path / "dvc_datasets" / dataset_id).mkdir(parents=True)
    provider = FakeProvider()
    service = DVCAssessmentService(tmp_path, provider=provider)
    result = service.pre_analysis(
        dataset_id,
        PreclinicalStudyContext(study_id="study-1"),
    )

    assert provider.context_frameworks == ("prepare-v1", "arrive-v2")
    target = tmp_path / result.relative_path
    assert target.is_file()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["checkpoint"] == "pre_analysis"
    assert payload["assessments"][0]["framework"] == "PREPARE"


def test_post_analysis_builds_bundle_and_index(tmp_path: Path):
    dataset_id = "dvc-00000000-0000-0000-0000-000000000000"
    dataset_dir = tmp_path / "dvc_datasets" / dataset_id
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (dataset_dir / "measurements.csv").write_text("value\n1\n", encoding="utf-8")
    (dataset_dir / "events.csv").write_text("event\n", encoding="utf-8")

    analysis_dir = tmp_path / "dvc_analyses" / "dvc-exec-1"
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "provenance.json").write_text(
        json.dumps(
            {
                "execution_id": "dvc-exec-1",
                "dataset_id": dataset_id,
                "operation": "check_data_sanity",
            }
        ),
        encoding="utf-8",
    )
    (analysis_dir / "result.json").write_text(
        json.dumps(
            {
                "execution_id": "dvc-exec-1",
                "dataset_id": dataset_id,
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )

    provider = FakeProvider()
    result = DVCAssessmentService(tmp_path, provider=provider).post_analysis(dataset_id)

    assert provider.bundle_frameworks == ("arrive-v2", "mnms-v1")
    assert provider.bundle_dir is not None
    assert (provider.bundle_dir / "analysis-index.json").is_file()
    index = json.loads((provider.bundle_dir / "analysis-index.json").read_text())
    assert index[0]["operation"] == "check_data_sanity"
    assert (tmp_path / result.relative_path).is_file()


def test_post_analysis_before_completed_analysis_fails_closed(tmp_path: Path):
    dataset_id = "dvc-00000000-0000-0000-0000-000000000000"
    (tmp_path / "dvc_datasets" / dataset_id).mkdir(parents=True)

    with pytest.raises(DVCAnalysisBlockedError) as exc:
        DVCAssessmentService(tmp_path, provider=FakeProvider()).post_analysis(dataset_id)

    assert exc.value.blockers == [
        "At least one completed analysis is required before post-analysis."
    ]
