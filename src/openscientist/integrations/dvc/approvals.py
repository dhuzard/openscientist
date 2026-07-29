"""Read-only approval records for governed DVC analyses.

Approval files are created by a trusted UI or workflow outside the agent MCP tool.
The agent may reference an approval id but cannot create or modify approvals here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from openscientist.integrations.dvc.execution import DVCAnalysisApproval


class DVCApprovalNotFoundError(RuntimeError):
    """No trusted approval record exists for the supplied id."""


class FileDVCApprovalStore:
    def __init__(self, job_dir: Path) -> None:
        self.root = Path(job_dir) / "dvc_approvals"

    def resolve(self, approval_id: str) -> DVCAnalysisApproval:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", approval_id):
            raise DVCApprovalNotFoundError("Invalid DVC approval id.")
        path = (self.root / f"{approval_id}.json").resolve()
        root = self.root.resolve()
        if root not in path.parents or not path.is_file():
            raise DVCApprovalNotFoundError(f"DVC approval not found: {approval_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            approval = DVCAnalysisApproval.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            raise DVCApprovalNotFoundError(
                f"DVC approval record is invalid: {approval_id}"
            ) from exc
        if approval.approval_id != approval_id:
            raise DVCApprovalNotFoundError("DVC approval id does not match its record.")
        return approval
