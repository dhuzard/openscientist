"""DVC registration for the generic governed assay kernel."""

from __future__ import annotations

from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

from openscientist.assays import (
    AssayAdapter,
    AssayRegistry,
    EvidencePattern,
    ExecutableValidator,
    GatewayAction,
    OperationContract,
    ReviewPanelSpec,
)
from openscientist.integrations.dvc.validators import (
    VALIDATOR_VERSION,
    validate_analysis_basis,
    validate_biological_time,
    validate_exact_windows,
    validate_experimental_unit,
    validate_invariance_and_rerun,
    validate_known_effect,
    validate_reconciliation_and_grid,
)

_STANDARD_INPUT_ROLES = ("normalized_measurements",)
_STANDARD_OUTPUT_EVIDENCE = ("result.json", "provenance.json")

DVC_VALIDATORS = MappingProxyType(
    {
        "dvc.analysis_basis": ExecutableValidator(
            "dvc.analysis_basis", VALIDATOR_VERSION, validate_analysis_basis
        ),
        "dvc.known_effect": ExecutableValidator(
            "dvc.known_effect", VALIDATOR_VERSION, validate_known_effect
        ),
        "dvc.experimental_unit": ExecutableValidator(
            "dvc.experimental_unit", VALIDATOR_VERSION, validate_experimental_unit
        ),
        "dvc.reconciliation_grid": ExecutableValidator(
            "dvc.reconciliation_grid", VALIDATOR_VERSION, validate_reconciliation_and_grid
        ),
        "dvc.biological_time": ExecutableValidator(
            "dvc.biological_time", VALIDATOR_VERSION, validate_biological_time
        ),
        "dvc.exact_windows": ExecutableValidator(
            "dvc.exact_windows", VALIDATOR_VERSION, validate_exact_windows
        ),
        "dvc.invariance": ExecutableValidator(
            "dvc.invariance", VALIDATOR_VERSION, validate_invariance_and_rerun
        ),
    }
)


class _GatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DVCConnectionActionRequest(_GatewayRequest):
    connection_id: str = Field(min_length=1, max_length=100)


class DVCSearchCagesRequest(DVCConnectionActionRequest):
    patterns: list[str] = Field(min_length=1, max_length=20)


DVC_OPERATION_CONTRACTS: dict[str, OperationContract] = {
    "check_data_sanity": OperationContract(
        operation_id="check_data_sanity",
        display_name="Check data sanity",
        contract_version="1.0.0",
        input_roles=_STANDARD_INPUT_ROLES,
        required_context=(),
        approval_required=False,
        output_evidence=_STANDARD_OUTPUT_EVIDENCE,
        numerical_tolerance="not_applicable",
        validator_ids=(
            "dvc.analysis_basis",
            "dvc.experimental_unit",
            "dvc.reconciliation_grid",
            "dvc.invariance",
        ),
    ),
    "summarize_time_bins": OperationContract(
        operation_id="summarize_time_bins",
        display_name="Summarize time bins",
        contract_version="1.0.0",
        input_roles=_STANDARD_INPUT_ROLES,
        required_context=("design.experimental_unit",),
        approval_required=True,
        output_evidence=_STANDARD_OUTPUT_EVIDENCE,
        numerical_tolerance="canonical_json_exact_against_pinned_udwa",
        validator_ids=(
            "dvc.analysis_basis",
            "dvc.experimental_unit",
            "dvc.reconciliation_grid",
            "dvc.invariance",
        ),
    ),
    "summarize_light_dark": OperationContract(
        operation_id="summarize_light_dark",
        display_name="Summarize light and dark phases",
        contract_version="1.0.0",
        input_roles=_STANDARD_INPUT_ROLES,
        required_context=(
            "design.experimental_unit",
            "environment.timezone",
            "environment.light_schedule",
        ),
        approval_required=True,
        output_evidence=_STANDARD_OUTPUT_EVIDENCE,
        numerical_tolerance="canonical_json_exact_against_pinned_udwa",
        validator_ids=(
            "dvc.analysis_basis",
            "dvc.experimental_unit",
            "dvc.reconciliation_grid",
            "dvc.biological_time",
            "dvc.invariance",
        ),
    ),
    "summarize_circadian_cosinor": OperationContract(
        operation_id="summarize_circadian_cosinor",
        display_name="Summarize circadian cosinor",
        contract_version="1.0.0",
        input_roles=_STANDARD_INPUT_ROLES,
        required_context=(
            "design.experimental_unit",
            "environment.timezone",
            "environment.light_schedule",
            "acquisition.temporal_resolution",
        ),
        approval_required=True,
        output_evidence=_STANDARD_OUTPUT_EVIDENCE,
        numerical_tolerance="canonical_json_exact_against_pinned_udwa",
        validator_ids=(
            "dvc.analysis_basis",
            "dvc.experimental_unit",
            "dvc.reconciliation_grid",
            "dvc.biological_time",
            "dvc.exact_windows",
            "dvc.invariance",
        ),
    ),
}

DVC_ADAPTER = AssayAdapter(
    adapter_id="dvc",
    display_name="Digital Ventilated Cage activity",
    adapter_version="1.0.0",
    dataset_id_pattern=r"dvc-[0-9a-fA-F-]{36}",
    operation_contracts=MappingProxyType(DVC_OPERATION_CONTRACTS),
    evidence_patterns=(
        EvidencePattern(
            "dataset_manifest",
            "dvc_datasets/*/manifest.json",
            schema_id="openscientist-dvc-dataset/0.1",
        ),
        EvidencePattern("dataset_supporting_asset", "dvc_datasets/**/*", required=False),
        EvidencePattern("normalized_measurements", "dvc_datasets/*/measurements.*"),
        EvidencePattern("normalized_events", "dvc_datasets/*/events.csv", required=False),
        EvidencePattern(
            "analysis_result",
            "dvc_analyses/*/result.json",
            schema_id="openscientist-dvc-analysis-result/0.1",
        ),
        EvidencePattern(
            "analysis_provenance",
            "dvc_analyses/*/provenance.json",
            schema_id="openscientist-dvc-analysis-provenance/0.1",
        ),
        EvidencePattern("assessment", "dvc_assessments/*.json"),
        EvidencePattern("approval", "dvc_approvals/approval-*.json"),
        EvidencePattern("audit_bundle", "dvc_bundles/**/*", required=False),
        EvidencePattern("legacy_workflow", "dvc_workflow.json", required=False),
        EvidencePattern(
            "analysis_run",
            "assay_runs/*/run.json",
            schema_id="openscientist-analysis-run/1.0",
        ),
        EvidencePattern("plot", "plots/**/*", required=False),
        EvidencePattern("provenance", "provenance/**/*", required=False),
        EvidencePattern("final_report_markdown", "final_report.md", required=False),
        EvidencePattern("final_report_html", "final_report.html", required=False),
        EvidencePattern("final_report_pdf", "final_report.pdf", required=False),
        EvidencePattern("evidence_plan", "EVIDENCE_PLAN.md", required=False),
        EvidencePattern("openscientist_audit", ".openscientist/**/*", required=False),
        EvidencePattern("real_validation_report", "DVC_REAL_VALIDATION_REPORT.md", required=False),
        EvidencePattern("validation_manifest", "dvc_validation_manifest.json", required=False),
        EvidencePattern("udwa_parity", "dvc_udwa_parity.json", required=False),
    ),
    manifest_schemas=(
        "openscientist-dvc-dataset/0.1",
        "openscientist-dvc-analysis-result/0.1",
        "openscientist-dvc-analysis-provenance/0.1",
        "openscientist-analysis-run/1.0",
        "openscientist-dvc-workflow-state/0.2",
        "openscientist-dvc-approval-audit/0.1",
    ),
    gateway_actions=(
        GatewayAction(
            action="test_connection",
            permission="dvc:read",
            handler_path="openscientist.integrations.dvc.service.DVCAcquisitionService",
            request_model_path=(
                "openscientist.integrations.dvc.adapter.DVCConnectionActionRequest"
            ),
            service_method="test_connection",
            mutating=False,
            requires_connection=True,
            invocation="kwargs",
        ),
        GatewayAction(
            action="list_metrics",
            permission="dvc:read",
            handler_path="openscientist.integrations.dvc.service.DVCAcquisitionService",
            request_model_path=(
                "openscientist.integrations.dvc.adapter.DVCConnectionActionRequest"
            ),
            service_method="list_metrics",
            mutating=False,
            requires_connection=True,
            invocation="kwargs",
        ),
        GatewayAction(
            action="search_cages",
            permission="dvc:read",
            handler_path="openscientist.integrations.dvc.service.DVCAcquisitionService",
            request_model_path="openscientist.integrations.dvc.adapter.DVCSearchCagesRequest",
            service_method="search_cages",
            mutating=False,
            requires_connection=True,
            invocation="kwargs",
        ),
        GatewayAction(
            action="import_dataset",
            permission="dvc:import",
            handler_path="openscientist.integrations.dvc.service.DVCAcquisitionService",
            request_model_path="openscientist.integrations.dvc.models.DVCImportRequest",
            service_method="import_dataset",
            mutating=True,
            requires_connection=True,
        ),
        GatewayAction(
            action="execute",
            permission="dvc:analyze",
            handler_path="openscientist.integrations.dvc.execution.DVCAnalysisService",
            request_model_path="openscientist.integrations.dvc.execution.DVCAnalysisRequest",
            service_method="execute",
            mutating=True,
        ),
    ),
    review_panel=ReviewPanelSpec(
        panel_id="assay-governance",
        title="Governed DVC analysis",
        summary_fields=(
            "dataset_id",
            "operation_id",
            "contract_version",
            "required_context",
            "parameters",
            "pre_analysis_checkpoint_id",
            "evidence",
        ),
        checkpoint_globs=("dvc_assessments/dvc-assess-*.json",),
        approval_create_handler_path=(
            "openscientist.integrations.dvc.approvals.create_dvc_approval_record"
        ),
        approval_list_handler_path=(
            "openscientist.integrations.dvc.approvals.list_dvc_pre_analysis_checkpoints"
        ),
        context_loader_handler_path=(
            "openscientist.integrations.dvc.approvals.load_checkpoint_context"
        ),
    ),
    validators=DVC_VALIDATORS,
)


def register_dvc_adapter(registry: AssayRegistry) -> AssayAdapter:
    return registry.register(DVC_ADAPTER)
