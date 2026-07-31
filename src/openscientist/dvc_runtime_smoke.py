"""Fail-closed runtime smoke test for the governed DVC integration.

The command is shipped in the agent image and is intentionally limited to
import and registration checks. It never connects to DVC or reads credentials.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

from openscientist.integrations.udwa import require_compatible_udwa

REQUIRED_DVC_TOOLS = (
    "dvc_test_connection",
    "dvc_list_metrics",
    "dvc_search_cages",
    "dvc_import_dataset",
    "dvc_assess_pre_analysis",
    "dvc_run_analysis",
    "dvc_assess_post_analysis",
)
APPROVAL_ROUTE = "/api/v1/dvc/jobs/{job_id}/approvals"


def collect_runtime_report() -> dict[str, Any]:
    """Require every pinned runtime boundary and return a redaction-safe report."""

    # The standalone MCP server intentionally fails closed when a real job
    # identity is absent. Give this isolated import-only smoke check a synthetic
    # identity so the image can verify tool registration without credentials or
    # a running job.
    os.environ.setdefault("OPENSCIENTIST_JOB_ID", "dvc-runtime-smoke")
    os.environ.setdefault("OPENSCIENTIST_JOB_DIR", "/tmp/openscientist-dvc-runtime-smoke")

    from openscientist.api.router import api_router
    from openscientist_tools import dvc as dvc_tools

    missing_tools = [
        name for name in REQUIRED_DVC_TOOLS if not callable(getattr(dvc_tools, name, None))
    ]
    if missing_tools:
        raise RuntimeError("Missing DVC MCP tools: " + ", ".join(missing_tools))

    route_paths = {getattr(route, "path", None) for route in api_router.routes}
    if APPROVAL_ROUTE not in route_paths:
        raise RuntimeError(f"Missing DVC approval route: {APPROVAL_ROUTE}")

    compatibility = require_compatible_udwa()
    return {
        "ok": True,
        "approval_route": APPROVAL_ROUTE,
        "dvc_tools": list(REQUIRED_DVC_TOOLS),
        "udwa": asdict(compatibility),
    }


def main() -> None:
    print(json.dumps(collect_runtime_report(), sort_keys=True))


if __name__ == "__main__":
    main()
