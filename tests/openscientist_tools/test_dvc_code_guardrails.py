"""Fail-closed safeguards for ad hoc code in governed DVC jobs."""

from __future__ import annotations

import json
from pathlib import Path

from openscientist_tools.code_exec import _dvc_code_guardrail

_DATASET_ID = "dvc-00000000-0000-0000-0000-000000000001"


def _manifest(job_dir: Path, *, include_data_science: bool) -> None:
    skills = [{"key": "domain--digital-ventilated-cage-analysis"}]
    if include_data_science:
        skills.append({"key": "domain--data-science"})
    (job_dir / ".openscientist_skill_manifest.json").write_text(
        json.dumps(skills),
        encoding="utf-8",
    )
    (job_dir / "dvc_datasets" / _DATASET_ID).mkdir(parents=True)


def _verified_schedule(job_dir: Path) -> None:
    analysis_dir = job_dir / "dvc_analyses" / "dvc-exec-test"
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "provenance.json").write_text(
        json.dumps(
            {
                "scientific_prerequisites": {
                    "environment.light_schedule": {
                        "status": "recorded",
                        "source": "approved protocol",
                        "value_sha256": "a" * 64,
                    },
                    "environment.timezone": {
                        "status": "recorded",
                        "source": "approved protocol",
                        "value_sha256": "b" * 64,
                    }
                },
                "dataset_id": _DATASET_ID,
            }
        ),
        encoding="utf-8",
    )


def test_governed_dvc_code_requires_explicit_ungoverned_scope(tmp_path: Path) -> None:
    _manifest(tmp_path, include_data_science=True)

    blocker, notice = _dvc_code_guardrail(
        tmp_path,
        code="print(data.shape)",
        description="Inspect the imported data",
    )

    assert "Label its description explicitly" in (blocker or "")
    assert notice is None


def test_ungoverned_statistics_require_data_science_assignment(tmp_path: Path) -> None:
    _manifest(tmp_path, include_data_science=False)

    blocker, notice = _dvc_code_guardrail(
        tmp_path,
        code="from scipy.stats import pearsonr",
        description="Exploratory correlation",
    )

    assert "`data-science` skill" in (blocker or "")
    assert notice is None


def test_placeholder_dark_onset_cannot_drive_circadian_plot(tmp_path: Path) -> None:
    _manifest(tmp_path, include_data_science=True)

    blocker, notice = _dvc_code_guardrail(
        tmp_path,
        code="plt.plot(hour_from_dark_onset, activity)",
        description="Exploratory circadian plot centered on assumed dark onset",
    )

    assert "verified, source-backed local light schedule" in (blocker or "")
    assert "Placeholder assumptions are not allowed" in (blocker or "")
    assert notice is None


def test_verified_schedule_allows_labelled_exploration_but_marks_scope(
    tmp_path: Path,
) -> None:
    _manifest(tmp_path, include_data_science=True)
    _verified_schedule(tmp_path)

    blocker, notice = _dvc_code_guardrail(
        tmp_path,
        code="plt.plot(zt, activity)",
        description="Exploratory circadian plot",
    )

    assert blocker is None
    assert "must not be presented as governed scientific evidence" in (notice or "")


def test_schedule_from_another_dataset_does_not_authorize_biological_time_code(
    tmp_path: Path,
) -> None:
    _manifest(tmp_path, include_data_science=True)
    _verified_schedule(tmp_path)
    other_dataset = "dvc-00000000-0000-0000-0000-000000000002"
    (tmp_path / "dvc_datasets" / other_dataset).mkdir()

    blocker, notice = _dvc_code_guardrail(
        tmp_path,
        code=f"plt.plot(zt, activity)  # {other_dataset}",
        description="Exploratory circadian plot",
    )

    assert other_dataset in (blocker or "")
    assert notice is None


def test_multiple_datasets_require_explicit_dataset_for_biological_time_code(
    tmp_path: Path,
) -> None:
    _manifest(tmp_path, include_data_science=True)
    _verified_schedule(tmp_path)
    (tmp_path / "dvc_datasets" / "dvc-00000000-0000-0000-0000-000000000002").mkdir()

    blocker, notice = _dvc_code_guardrail(
        tmp_path,
        code="plt.plot(zt, activity)",
        description="Exploratory circadian plot",
    )

    assert "exact DVC dataset is ambiguous" in (blocker or "")
    assert notice is None
