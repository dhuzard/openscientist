from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from openscientist.api.endpoints.dvc import ApprovalCreate, create_dvc_approval
from openscientist.integrations.dvc.approvals import FileDVCApprovalStore
from openscientist.integrations.dvc.execution import (
    canonical_context_sha256,
    canonical_parameters_sha256,
)
from openscientist.preclinical_context.models import PreclinicalStudyContext


class FakeSession:
    def __init__(self, job):
        self.job = job

    async def execute(self, _statement, _parameters=None):
        return None

    async def get(self, _model, _job_id):
        return self.job


@pytest.mark.asyncio
async def test_api_created_approval_is_resolvable(tmp_path, monkeypatch):
    jobs_root = tmp_path / "jobs"
    monkeypatch.setenv("OPENSCIENTIST_JOBS_DIR", str(jobs_root))
    job_id = uuid4()
    user_id = uuid4()
    dataset_id = f"dvc-{uuid4()}"
    checkpoint_id = f"dvc-assess-{uuid4()}"
    context = PreclinicalStudyContext(study_id="study-1")
    job_dir = jobs_root / str(job_id)
    assessment_dir = job_dir / "dvc_assessments"
    assessment_dir.mkdir(parents=True)
    (assessment_dir / f"{checkpoint_id}.json").write_text(
        json.dumps(
            {
                "checkpoint_id": checkpoint_id,
                "checkpoint": "pre_analysis",
                "dataset_id": dataset_id,
                "context_sha256": canonical_context_sha256(context),
                "assessments": [
                    {"framework": "prepare-v1"},
                    {"framework": "arrive-v2"},
                ],
            }
        ),
        encoding="utf-8",
    )

    body = ApprovalCreate(
        dataset_id=dataset_id,
        operation="summarize_light_dark",
        context=context,
        parameters={"bin_minutes": 60},
        pre_analysis_checkpoint_id=checkpoint_id,
    )
    response = await create_dvc_approval(
        job_id,
        body,
        current_user=SimpleNamespace(id=user_id, email="scientist@example.org"),
        session=FakeSession(SimpleNamespace(owner_id=user_id)),
    )

    approval = FileDVCApprovalStore(job_dir).resolve(response.approval_id)
    assert approval.operation == "summarize_light_dark"
    assert approval.approved_by == "scientist@example.org"
    assert approval.dataset_id == dataset_id
    assert approval.pre_analysis_checkpoint_id == checkpoint_id
    assert approval.parameters_sha256 == canonical_parameters_sha256({"bin_minutes": 60})
    audit = json.loads(
        (job_dir / "dvc_approvals" / f"{response.approval_id}.audit.json").read_text()
    )
    assert audit["pre_analysis_checkpoint_id"] == checkpoint_id
    assert audit["assessment_frameworks"] == ["prepare-v1", "arrive-v2"]


@pytest.mark.asyncio
async def test_approval_rejects_other_users(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSCIENTIST_JOBS_DIR", str(tmp_path / "jobs"))
    with pytest.raises(HTTPException) as exc:
        await create_dvc_approval(
            uuid4(),
            ApprovalCreate(
                dataset_id=f"dvc-{uuid4()}",
                operation="summarize_time_bins",
                context=PreclinicalStudyContext(study_id="study-1"),
                pre_analysis_checkpoint_id=f"dvc-assess-{uuid4()}",
            ),
            current_user=SimpleNamespace(id=uuid4(), email="other@example.org"),
            session=FakeSession(SimpleNamespace(owner_id=uuid4())),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_approval_requires_matching_pre_analysis_checkpoint(tmp_path, monkeypatch):
    jobs_root = tmp_path / "jobs"
    monkeypatch.setenv("OPENSCIENTIST_JOBS_DIR", str(jobs_root))
    job_id = uuid4()
    user_id = uuid4()
    dataset_id = f"dvc-{uuid4()}"
    checkpoint_id = f"dvc-assess-{uuid4()}"
    context = PreclinicalStudyContext(study_id="study-1")
    assessment_dir = jobs_root / str(job_id) / "dvc_assessments"
    assessment_dir.mkdir(parents=True)
    (assessment_dir / f"{checkpoint_id}.json").write_text(
        json.dumps(
            {
                "checkpoint_id": checkpoint_id,
                "checkpoint": "post_analysis",
                "dataset_id": dataset_id,
                "context_sha256": canonical_context_sha256(context),
                "assessments": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc:
        await create_dvc_approval(
            job_id,
            ApprovalCreate(
                dataset_id=dataset_id,
                operation="summarize_time_bins",
                context=context,
                pre_analysis_checkpoint_id=checkpoint_id,
            ),
            current_user=SimpleNamespace(id=user_id, email="scientist@example.org"),
            session=FakeSession(SimpleNamespace(owner_id=user_id)),
        )
    assert exc.value.status_code == 400
    assert "not a pre-analysis" in exc.value.detail
