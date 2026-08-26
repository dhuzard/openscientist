from __future__ import annotations

from types import MappingProxyType

import pytest

from openscientist.assays import (
    AssayAdapter,
    AssayAlreadyRegisteredError,
    AssayRegistry,
    ExecutableValidator,
    OperationContract,
    ReviewPanelSpec,
    ValidationFinding,
    ValidationResult,
    get_assay_registry,
)


def test_dvc_is_explicitly_registered_with_generic_consumer_metadata():
    adapter = get_assay_registry().require("dvc")

    assert adapter.adapter_id == "dvc"
    assert adapter.validate_dataset_id("dvc-00000000-0000-0000-0000-000000000000")
    assert "summarize_light_dark" in adapter.operation_contracts
    assert {item.action for item in adapter.gateway_actions} == {
        "test_connection",
        "list_metrics",
        "search_cages",
        "import_dataset",
        "execute",
    }
    assert any(item.glob == "dvc_bundles/**/*" for item in adapter.evidence_patterns)
    assert adapter.review_panel.approval_create_handler_path is not None
    assert adapter.review_panel.checkpoint_globs == ("dvc_assessments/dvc-assess-*.json",)


def test_adapter_fails_when_contract_references_missing_validator():
    contract = OperationContract(
        operation_id="inspect_data",
        display_name="Inspect data",
        contract_version="1.0.0",
        input_roles=("measurements",),
        required_context=(),
        approval_required=False,
        output_evidence=("result",),
        numerical_tolerance="exact",
        validator_ids=("missing",),
    )

    with pytest.raises(ValueError, match="unregistered validators"):
        AssayAdapter(
            adapter_id="test_assay",
            display_name="Test assay",
            adapter_version="1.0.0",
            dataset_id_pattern=r"test-[a-z0-9]+",
            operation_contracts=MappingProxyType({"inspect_data": contract}),
            evidence_patterns=(),
            manifest_schemas=(),
            gateway_actions=(),
            review_panel=ReviewPanelSpec(
                panel_id="review",
                title="Review",
                summary_fields=("dataset_id",),
            ),
        )


def test_executable_validator_checks_result_identity():
    def validate(_payload):
        return ValidationResult(
            validator_id="schema",
            validator_version="1.0.0",
            passed=True,
            findings=[ValidationFinding(check_id="shape", passed=True, message="valid")],
        )

    validator = ExecutableValidator("schema", "1.0.0", validate)
    assert validator({"rows": []}).passed


def test_registry_registration_is_idempotent_but_rejects_replacement():
    default = get_assay_registry().require("dvc")
    registry = AssayRegistry()

    assert registry.register(default) is default
    assert registry.register(default) is default

    replacement = AssayAdapter(
        adapter_id="dvc",
        display_name="Replacement",
        adapter_version="2.0.0",
        dataset_id_pattern=default.dataset_id_pattern,
        operation_contracts=default.operation_contracts,
        evidence_patterns=default.evidence_patterns,
        manifest_schemas=default.manifest_schemas,
        gateway_actions=default.gateway_actions,
        review_panel=default.review_panel,
        validators=default.validators,
    )
    with pytest.raises(AssayAlreadyRegisteredError):
        registry.register(replacement)
