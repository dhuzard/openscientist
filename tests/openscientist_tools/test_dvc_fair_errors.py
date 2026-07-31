from openscientist.integrations.fair_prepare import (
    FairPrepareError,
    FairPrepareFailureKind,
)
from openscientist_tools import dvc as dvc_tools
from openscientist_tools.dvc import _error


def test_dvc_tool_error_preserves_actionable_fair_vcg_classification():
    error = FairPrepareError(
        "FAIR-VCG GET /openapi.json service discovery failed",
        kind=FairPrepareFailureKind.DNS_SERVICE_DISCOVERY,
        retryable=True,
        endpoint="GET /openapi.json",
        action="Attach the application to the FAIR runtime network.",
    )

    assert _error(error) == {
        "ok": False,
        "error": "FAIR-VCG GET /openapi.json service discovery failed",
        "error_type": "FairPrepareError",
        "error_code": "fair_vcg.dns_service_discovery",
        "failure_kind": "dns_service_discovery",
        "retryable": True,
        "endpoint": "GET /openapi.json",
        "action": "Attach the application to the FAIR runtime network.",
    }


def test_invalid_context_schema_never_reaches_fair_provider(monkeypatch):
    def fail_if_called():
        raise AssertionError("assessment service must not be created")

    monkeypatch.setattr(dvc_tools, "_assessment_service", fail_if_called)

    result = dvc_tools.dvc_assess_pre_analysis(
        "dvc-00000000-0000-0000-0000-000000000000",
        {
            "study_id": "study-1",
            "objective": {"value": "Test DVC activity", "state": "recorded"},
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == "ValidationError"
    assert "objective.status" not in result["error"]
    assert "objective.state" in result["error"]
