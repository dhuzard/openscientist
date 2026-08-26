from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from openscientist.artifact_packager import create_assay_evidence_bundle_zip
from openscientist.assay_gateway import dispatch_assay_action
from openscientist.assays import (
    AnalysisRunStage,
    AnalysisRunStore,
    ApprovalDecision,
    AssayRegistry,
    get_assay_registry,
    make_analysis_run_id,
)
from openscientist.integrations.open_field import (
    OPEN_FIELD_ADAPTER,
    OpenFieldAnalysisError,
    OpenFieldAnalysisRequest,
    OpenFieldAnalysisService,
    OpenFieldImportMetadata,
    OpenFieldImportRequest,
    open_field_contract_sha256,
    register_open_field_adapter,
)

_CONTEXT_SHA256 = "c" * 64
_PARAMETERS_SHA256 = "d" * 64
_STUDY_ID = "study-open-field"


def _metadata(**updates: object) -> OpenFieldImportMetadata:
    values: dict[str, object] = {
        "frame_rate_hz": 2.0,
        "timezone": "Europe/Paris",
        "clock_id": "camera-clock-1",
        "clock_synchronized": True,
        "coordinate_unit": "cm",
        "timestamp_unit": "seconds",
        "experimental_unit": "subject",
        "observational_unit": "subject_session",
        "analysis_unit": "subject",
    }
    values.update(updates)
    return OpenFieldImportMetadata.model_validate(values)


def _write_tracking(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "subject_id,session_id,timestamp,x,y,zone\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


def _import(job_dir: Path, rows: list[str], **metadata: object):
    _write_tracking(job_dir / "uploads" / "tracking.csv", rows)
    service = OpenFieldAnalysisService(job_dir)
    result = service.import_dataset(
        OpenFieldImportRequest(
            source_relative_path="uploads/tracking.csv",
            metadata=_metadata(**metadata),
        )
    )
    return service, result


def _request(dataset_id: str, operation_id: str) -> OpenFieldAnalysisRequest:
    return OpenFieldAnalysisRequest(
        dataset_id=dataset_id,
        study_id=_STUDY_ID,
        run_id=make_analysis_run_id(
            study_id=_STUDY_ID,
            assay_id="open-field",
            dataset_id=dataset_id,
            operation_id=operation_id,
            context_sha256=_CONTEXT_SHA256,
            parameters_sha256=_PARAMETERS_SHA256,
        ),
        context_sha256=_CONTEXT_SHA256,
        parameters_sha256=_PARAMETERS_SHA256,
        analysis_unit="subject",
    )


def _approve(
    job_dir: Path, request: OpenFieldAnalysisRequest, operation_id: str
) -> AnalysisRunStore:
    store = AnalysisRunStore.for_analysis(
        job_dir,
        study_id=request.study_id,
        assay_id="open-field",
        dataset_id=request.dataset_id,
        operation_id=operation_id,
        context_sha256=request.context_sha256,
        parameters_sha256=request.parameters_sha256,
    )
    checkpoint_id = f"checkpoint-{operation_id}"
    store.record_dataset(request.dataset_id)
    store.record_checkpoint(
        checkpoint_id,
        is_pre=True,
        context_sha256=request.context_sha256,
    )
    decision = _decision(request, operation_id)
    store.record_approval(
        decision.approval_id,
        checkpoint_id=checkpoint_id,
        dataset_id=request.dataset_id,
        decision=decision,
    )
    return store


def _decision(
    request: OpenFieldAnalysisRequest,
    operation_id: str,
    *,
    approval_id: str | None = None,
    contract_sha256: str | None = None,
) -> ApprovalDecision:
    return ApprovalDecision(
        approval_id=approval_id or f"approval-{operation_id}",
        run_id=request.run_id,
        assay_id="open-field",
        dataset_id=request.dataset_id,
        operation_id=operation_id,
        contract_sha256=contract_sha256 or open_field_contract_sha256(operation_id),
        context_sha256=request.context_sha256,
        parameters_sha256=request.parameters_sha256,
        decided_by="scientist-1",
        decided_at=datetime.now(UTC),
        decision="approved",
        rationale="Reviewed exact open-field run contract.",
    )


def test_golden_import_distance_and_time_weighted_zone_occupancy(tmp_path: Path) -> None:
    service, dataset = _import(
        tmp_path,
        [
            "mouse-2,s1,1.0,1,0,edge",
            "mouse-1,s1,0.0,0,0,edge",
            "mouse-1,s1,0.5,3,4,center",
            "mouse-1,s1,1.0,6,8,center",
            "mouse-2,s1,0.0,0,0,edge",
            "mouse-2,s1,0.5,0,1,center",
        ],
    )
    sanity_request = _request(dataset.dataset_id, "check_data_sanity")
    distance_request = _request(dataset.dataset_id, "summarize_distance")
    occupancy_request = _request(dataset.dataset_id, "summarize_zone_occupancy")
    distance_store = _approve(tmp_path, distance_request, "summarize_distance")
    _approve(tmp_path, occupancy_request, "summarize_zone_occupancy")

    sanity = service.check_data_sanity(sanity_request)
    distance = service.summarize_distance(distance_request)
    occupancy = service.summarize_zone_occupancy(occupancy_request)

    assert sanity.passed
    assert dataset.subject_count == 2
    assert distance.result["subject_distance"][0] == {
        "subject_id": "mouse-1",
        "distance": 10.0,
        "session_count": 1,
    }
    assert distance.result["subject_distance"][1]["subject_id"] == "mouse-2"
    assert distance.result["subject_distance"][1]["session_count"] == 1
    assert distance.result["subject_distance"][1]["distance"] == pytest.approx(1 + 2**0.5)
    mouse_1 = [
        row for row in occupancy.result["subject_zone_occupancy"] if row["subject_id"] == "mouse-1"
    ]
    assert mouse_1 == [
        {
            "subject_id": "mouse-1",
            "zone": "center",
            "duration_seconds": 0.5,
            "proportion": 0.5,
            "session_count": 1,
        },
        {
            "subject_id": "mouse-1",
            "zone": "edge",
            "duration_seconds": 0.5,
            "proportion": 0.5,
            "session_count": 1,
        },
    ]
    run_state = distance_store.load()
    assert run_state.current_stage is AnalysisRunStage.ANALYZED
    assert {item.role for item in run_state.evidence} == {
        "analysis_result",
        "analysis_provenance",
    }
    assert (tmp_path / distance.provenance_relative_path).is_file()
    bundle = create_assay_evidence_bundle_zip(tmp_path, "job-open-field", "open-field")
    with zipfile.ZipFile(BytesIO(bundle.getvalue())) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("ASSAY_EVIDENCE_MANIFEST.json"))
    assert distance.result_relative_path in names
    assert distance.provenance_relative_path in names
    assert f"assay_runs/{distance_request.run_id}/run.json" in names
    assert manifest["complete"] is True
    assert manifest["assay_id"] == "open-field"


def test_missing_required_clock_and_frame_metadata_fails() -> None:
    with pytest.raises(ValidationError):
        OpenFieldImportMetadata.model_validate(
            {
                "timezone": "UTC",
                "coordinate_unit": "cm",
                "timestamp_unit": "seconds",
                "experimental_unit": "subject",
                "observational_unit": "subject_session",
                "analysis_unit": "subject",
            }
        )


def test_blocked_run_becomes_reviewable_then_generic_approval_allows_execution(
    tmp_path: Path,
) -> None:
    service, dataset = _import(
        tmp_path,
        [
            "mouse-1,s1,0,0,0,left",
            "mouse-1,s1,0.5,1,0,right",
            "mouse-1,s1,1,2,0,right",
        ],
    )
    request = _request(dataset.dataset_id, "summarize_distance")
    with pytest.raises(OpenFieldAnalysisError, match="approved analysis run"):
        service.summarize_distance(request)

    store = AnalysisRunStore.for_analysis(
        tmp_path,
        study_id=request.study_id,
        assay_id="open-field",
        dataset_id=request.dataset_id,
        operation_id="summarize_distance",
        context_sha256=request.context_sha256,
        parameters_sha256=request.parameters_sha256,
    )
    pending = store.load()
    assert pending.current_stage is AnalysisRunStage.PENDING_APPROVAL
    assert pending.datasets == [request.dataset_id]
    assert len(pending.checkpoints) == 1
    checkpoint_id = pending.checkpoints[0]
    wrong = _decision(
        request,
        "summarize_distance",
        approval_id="approval-wrong-contract",
        contract_sha256="e" * 64,
    )
    store.record_approval(
        wrong.approval_id,
        checkpoint_id=checkpoint_id,
        dataset_id=request.dataset_id,
        decision=wrong,
    )
    with pytest.raises(OpenFieldAnalysisError, match="exact contract"):
        service.summarize_distance(request)

    exact = _decision(
        request,
        "summarize_distance",
        approval_id="approval-exact-contract",
    )
    store.record_approval(
        exact.approval_id,
        checkpoint_id=checkpoint_id,
        dataset_id=request.dataset_id,
        decision=exact,
    )
    assert store.load().current_stage is AnalysisRunStage.APPROVED
    assert service.summarize_distance(request).passed
    assert store.load().current_stage is AnalysisRunStage.ANALYZED


@pytest.mark.parametrize(
    "rows, message",
    [
        (
            ["mouse-1,s1,0,0,0,edge", "mouse-1,s1,0.0,1,1,center"],
            "Duplicate subject-session timestamp",
        ),
        (["mouse-1,s1,0,NaN,0,edge"], "x must be finite"),
        (["mouse-1,s1,0,0,Infinity,edge"], "y must be finite"),
    ],
)
def test_import_rejects_duplicate_timestamps_and_invalid_coordinates(
    tmp_path: Path, rows: list[str], message: str
) -> None:
    _write_tracking(tmp_path / "uploads" / "tracking.csv", rows)
    service = OpenFieldAnalysisService(tmp_path)

    with pytest.raises(OpenFieldAnalysisError, match=message):
        service.import_dataset(
            OpenFieldImportRequest(
                source_relative_path="uploads/tracking.csv", metadata=_metadata()
            )
        )


def test_import_rejects_unsafe_and_symlink_escape_paths(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        OpenFieldImportRequest(
            source_relative_path="../outside.csv",
            metadata=_metadata(),
        )

    outside = tmp_path.parent / "outside.csv"
    outside.write_text("subject_id,session_id,timestamp,x,y\n", encoding="utf-8")
    link = tmp_path / "linked.csv"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks are unavailable on this platform")
    with pytest.raises(OpenFieldAnalysisError, match="inside the job directory"):
        OpenFieldAnalysisService(tmp_path).import_dataset(
            OpenFieldImportRequest(source_relative_path="linked.csv", metadata=_metadata())
        )


def test_unit_of_analysis_traps_fail_at_contract_boundary() -> None:
    with pytest.raises(ValidationError):
        OpenFieldAnalysisRequest.model_validate(
            {
                "dataset_id": "open-field-" + "a" * 24,
                "study_id": _STUDY_ID,
                "run_id": "r",
                "context_sha256": _CONTEXT_SHA256,
                "parameters_sha256": _PARAMETERS_SHA256,
                "analysis_unit": "frame",
            }
        )
    with pytest.raises(ValidationError):
        OpenFieldImportMetadata.model_validate(
            {
                **_metadata().model_dump(),
                "analysis_unit": "subject_session",
            }
        )


def test_sampling_perturbation_is_invariant_when_declared_and_mismatch_is_blocked(
    tmp_path: Path,
) -> None:
    fine_dir = tmp_path / "fine"
    coarse_dir = tmp_path / "coarse"
    fine_service, fine = _import(
        fine_dir,
        [
            "mouse-1,s1,0.0,0,0,left",
            "mouse-1,s1,0.5,1,0,left",
            "mouse-1,s1,1.0,2,0,right",
            "mouse-1,s1,1.5,3,0,right",
            "mouse-1,s1,2.0,4,0,right",
        ],
        frame_rate_hz=2.0,
    )
    coarse_service, coarse = _import(
        coarse_dir,
        [
            "mouse-1,s1,0.0,0,0,left",
            "mouse-1,s1,1.0,2,0,right",
            "mouse-1,s1,2.0,4,0,right",
        ],
        frame_rate_hz=1.0,
    )
    fine_request = _request(fine.dataset_id, "summarize_distance")
    coarse_request = _request(coarse.dataset_id, "summarize_distance")
    _approve(fine_dir, fine_request, "summarize_distance")
    _approve(coarse_dir, coarse_request, "summarize_distance")
    fine_result = fine_service.summarize_distance(fine_request)
    coarse_result = coarse_service.summarize_distance(coarse_request)
    assert fine_result.result["subject_distance"][0]["distance"] == 4.0
    assert coarse_result.result["subject_distance"][0]["distance"] == 4.0

    mismatched_service, mismatched = _import(
        tmp_path / "mismatch",
        [
            "mouse-1,s1,0,0,0,left",
            "mouse-1,s1,1,1,0,right",
            "mouse-1,s1,2,2,0,right",
        ],
        frame_rate_hz=10.0,
    )
    request = _request(mismatched.dataset_id, "check_data_sanity")
    assert not mismatched_service.check_data_sanity(request).passed
    distance_request = _request(mismatched.dataset_id, "summarize_distance")
    _approve(tmp_path / "mismatch", distance_request, "summarize_distance")
    with pytest.raises(OpenFieldAnalysisError, match="analysis is blocked"):
        mismatched_service.summarize_distance(distance_request)


def test_import_and_analysis_reruns_are_byte_deterministic(tmp_path: Path) -> None:
    rows = [
        "mouse-1,s1,0,0,0,left",
        "mouse-1,s1,0.5,1,0,left",
        "mouse-1,s1,1,2,0,right",
    ]
    service, first = _import(tmp_path, rows)
    second = service.import_dataset(
        OpenFieldImportRequest(source_relative_path="uploads/tracking.csv", metadata=_metadata())
    )
    request = _request(first.dataset_id, "summarize_distance")
    _approve(tmp_path, request, "summarize_distance")
    first_analysis = service.summarize_distance(request)
    first_bytes = (tmp_path / first_analysis.result_relative_path).read_bytes()
    second_analysis = service.summarize_distance(request)

    assert first == second
    assert first_analysis == second_analysis
    assert (tmp_path / second_analysis.result_relative_path).read_bytes() == first_bytes


def test_adapter_contracts_validators_and_registration_are_complete(tmp_path: Path) -> None:
    registry = AssayRegistry()
    assert register_open_field_adapter(registry) is OPEN_FIELD_ADAPTER
    assert register_open_field_adapter(registry) is OPEN_FIELD_ADAPTER
    assert registry.require("open-field") is OPEN_FIELD_ADAPTER
    assert get_assay_registry().require("open-field") is OPEN_FIELD_ADAPTER
    assert set(OPEN_FIELD_ADAPTER.operation_contracts) == {
        "import_dataset",
        "check_data_sanity",
        "summarize_distance",
        "summarize_zone_occupancy",
    }
    assert all(
        contract.validator_ids for contract in OPEN_FIELD_ADAPTER.operation_contracts.values()
    )

    _service, dataset = _import(
        tmp_path,
        ["mouse-1,s1,0,0,0,left", "mouse-1,s1,0.5,1,0,right"],
    )
    manifest = json.loads((tmp_path / dataset.manifest_relative_path).read_text(encoding="utf-8"))
    validation = OPEN_FIELD_ADAPTER.validators["open_field.import"](manifest)
    assert validation.passed


def test_generic_gateway_dispatches_adapter_without_core_changes(tmp_path: Path) -> None:
    _write_tracking(
        tmp_path / "uploads" / "gateway.csv",
        ["mouse-1,s1,0,0,0,left", "mouse-1,s1,0.5,1,0,right"],
    )
    action = next(
        item for item in OPEN_FIELD_ADAPTER.gateway_actions if item.action == "import_dataset"
    )
    request = OpenFieldImportRequest(
        source_relative_path="uploads/gateway.csv",
        metadata=_metadata(),
    )

    result = dispatch_assay_action(
        adapter=OPEN_FIELD_ADAPTER,
        action=action,
        job_dir=tmp_path,
        arguments=request.model_dump(mode="json"),
    )

    assert result["dataset_id"].startswith("open-field-")
    assert result["metadata"]["analysis_unit"] == "subject"
