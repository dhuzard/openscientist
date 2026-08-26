"""Open-field adapter descriptor for the generic governed assay kernel."""

from __future__ import annotations

from dataclasses import asdict
from types import MappingProxyType

from openscientist.assays import (
    AssayAdapter,
    AssayRegistry,
    EvidencePattern,
    ExecutableValidator,
    GatewayAction,
    OperationContract,
    ReviewPanelSpec,
    canonical_json_sha256,
)
from openscientist.integrations.open_field.validators import (
    VALIDATOR_VERSION,
    validate_distance,
    validate_import,
    validate_sanity,
    validate_zone_occupancy,
)

OPEN_FIELD_VALIDATORS = MappingProxyType(
    {
        "open_field.import": ExecutableValidator(
            "open_field.import", VALIDATOR_VERSION, validate_import
        ),
        "open_field.sanity": ExecutableValidator(
            "open_field.sanity", VALIDATOR_VERSION, validate_sanity
        ),
        "open_field.distance": ExecutableValidator(
            "open_field.distance", VALIDATOR_VERSION, validate_distance
        ),
        "open_field.zone_occupancy": ExecutableValidator(
            "open_field.zone_occupancy", VALIDATOR_VERSION, validate_zone_occupancy
        ),
    }
)

OPEN_FIELD_OPERATION_CONTRACTS = MappingProxyType(
    {
        "import_dataset": OperationContract(
            operation_id="import_dataset",
            display_name="Normalize derived tracking CSV",
            contract_version="1.0.0",
            input_roles=("derived_tracking_csv",),
            required_context=(
                "design.experimental_unit",
                "design.observational_unit",
                "design.analysis_unit",
                "environment.timezone",
                "acquisition.clock_id",
                "acquisition.clock_synchronized",
                "acquisition.frame_rate_hz",
                "acquisition.coordinate_unit",
            ),
            approval_required=False,
            output_evidence=("manifest.json", "tracking.csv"),
            numerical_tolerance="canonical_csv_exact",
            validator_ids=("open_field.import",),
        ),
        "check_data_sanity": OperationContract(
            operation_id="check_data_sanity",
            display_name="Check tracking and clock sanity",
            contract_version="1.0.0",
            input_roles=("normalized_tracking", "dataset_manifest"),
            required_context=(
                "design.analysis_unit",
                "acquisition.clock_synchronized",
                "acquisition.frame_rate_hz",
            ),
            approval_required=False,
            output_evidence=("result.json", "provenance.json"),
            numerical_tolerance="declared_frame_rate_relative_tolerance",
            validator_ids=("open_field.sanity",),
        ),
        "summarize_distance": OperationContract(
            operation_id="summarize_distance",
            display_name="Summarize path distance by subject",
            contract_version="1.0.0",
            input_roles=("normalized_tracking", "dataset_manifest"),
            required_context=(
                "design.analysis_unit",
                "acquisition.coordinate_unit",
                "acquisition.frame_rate_hz",
            ),
            approval_required=True,
            output_evidence=("result.json", "provenance.json"),
            numerical_tolerance="ieee754_deterministic_same_runtime",
            validator_ids=("open_field.distance",),
        ),
        "summarize_zone_occupancy": OperationContract(
            operation_id="summarize_zone_occupancy",
            display_name="Summarize time-weighted zone occupancy by subject",
            contract_version="1.0.0",
            input_roles=("normalized_tracking", "dataset_manifest"),
            required_context=(
                "design.analysis_unit",
                "acquisition.frame_rate_hz",
                "tracking.zone_labels",
            ),
            approval_required=True,
            output_evidence=("result.json", "provenance.json"),
            numerical_tolerance="interval_weighted_duration_1e-12",
            validator_ids=("open_field.zone_occupancy",),
        ),
    }
)

_SERVICE = "openscientist.integrations.open_field.service.OpenFieldAnalysisService"
_MODELS = "openscientist.integrations.open_field.models"

OPEN_FIELD_ADAPTER = AssayAdapter(
    adapter_id="open-field",
    display_name="Derived open-field tracking",
    adapter_version="1.0.0",
    dataset_id_pattern=r"open-field-[0-9a-f]{24}",
    operation_contracts=OPEN_FIELD_OPERATION_CONTRACTS,
    evidence_patterns=(
        EvidencePattern(
            "dataset_manifest",
            "open_field_datasets/*/manifest.json",
            schema_id="openscientist-open-field-dataset/1.0",
        ),
        EvidencePattern("normalized_tracking", "open_field_datasets/*/tracking.csv"),
        EvidencePattern(
            "analysis_result",
            "open_field_analyses/*/*/result.json",
            schema_id="openscientist-open-field-analysis/1.0",
        ),
        EvidencePattern(
            "analysis_provenance",
            "open_field_analyses/*/*/provenance.json",
            schema_id="openscientist-open-field-provenance/1.0",
        ),
        EvidencePattern(
            "analysis_run",
            "assay_runs/open-field-run-*/run.json",
            schema_id="openscientist-analysis-run/1.0",
        ),
    ),
    manifest_schemas=(
        "openscientist-open-field-dataset/1.0",
        "openscientist-open-field-analysis/1.0",
        "openscientist-open-field-provenance/1.0",
        "openscientist-analysis-run/1.0",
    ),
    gateway_actions=(
        GatewayAction(
            action="import_dataset",
            permission="open-field:import",
            handler_path=_SERVICE,
            request_model_path=f"{_MODELS}.OpenFieldImportRequest",
            service_method="import_dataset",
            mutating=True,
        ),
        GatewayAction(
            action="check_data_sanity",
            permission="open-field:analyze",
            handler_path=_SERVICE,
            request_model_path=f"{_MODELS}.OpenFieldAnalysisRequest",
            service_method="check_data_sanity",
            mutating=True,
        ),
        GatewayAction(
            action="summarize_distance",
            permission="open-field:analyze",
            handler_path=_SERVICE,
            request_model_path=f"{_MODELS}.OpenFieldAnalysisRequest",
            service_method="summarize_distance",
            mutating=True,
        ),
        GatewayAction(
            action="summarize_zone_occupancy",
            permission="open-field:analyze",
            handler_path=_SERVICE,
            request_model_path=f"{_MODELS}.OpenFieldAnalysisRequest",
            service_method="summarize_zone_occupancy",
            mutating=True,
        ),
    ),
    review_panel=ReviewPanelSpec(
        panel_id="assay-governance",
        title="Governed open-field analysis",
        summary_fields=(
            "dataset_id",
            "run_id",
            "operation_id",
            "analysis_unit",
            "frame_rate_hz",
            "timezone",
            "clock_id",
            "clock_synchronized",
            "coordinate_unit",
            "evidence",
        ),
    ),
    validators=OPEN_FIELD_VALIDATORS,
)


def register_open_field_adapter(registry: AssayRegistry) -> AssayAdapter:
    """Idempotently register the built-in open-field adapter."""

    return registry.register(OPEN_FIELD_ADAPTER)


def open_field_contract_sha256(operation_id: str) -> str:
    """Return the approval-binding hash for one registered operation contract."""

    contract = OPEN_FIELD_ADAPTER.require_operation(operation_id)
    return canonical_json_sha256(asdict(contract))
