from __future__ import annotations

from openscientist import dvc_runtime_smoke
from openscientist.integrations.udwa import UdwaCompatibilityReport


def test_runtime_smoke_covers_tools_router_and_udwa(monkeypatch) -> None:
    monkeypatch.setattr(
        dvc_runtime_smoke,
        "require_compatible_udwa",
        lambda: UdwaCompatibilityReport(
            distribution_version="0.1.0",
            pinned_commit="a" * 40,
            missing_imports=(),
            missing_operations=(),
        ),
    )

    report = dvc_runtime_smoke.collect_runtime_report()

    assert report["ok"] is True
    assert report["approval_route"] == "/api/v1/dvc/jobs/{job_id}/approvals"
    assert set(report["dvc_tools"]) == set(dvc_runtime_smoke.REQUIRED_DVC_TOOLS)
    assert report["udwa"]["missing_imports"] == ()
    assert report["udwa"]["missing_operations"] == ()
