"""Stdio entry point: ``python -m openscientist_tools``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openscientist_tools.server import mcp

#: Touched the first time a client asks for the tool inventory. The harness has
#: no way to ask omp which MCP servers it connected to, so the server records the
#: handshake itself and OmpAgent reads the marker (openscientist-io#263).
HANDSHAKE_MARKER = "mcp_handshake"


def _install_handshake_marker() -> None:
    job_dir = os.environ.get("OPENSCIENTIST_JOB_DIR")
    if not job_dir:
        return
    marker = Path(job_dir) / ".omp" / HANDSHAKE_MARKER
    original = mcp.list_tools

    async def list_tools_recording_handshake(*args: Any, **kwargs: Any) -> Any:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        except OSError:
            # The marker is diagnostics only; never fail a tools/list over it.
            pass
        return await original(*args, **kwargs)

    mcp.list_tools = list_tools_recording_handshake  # type: ignore[method-assign]


if __name__ == "__main__":
    _install_handshake_marker()
    mcp.run()
