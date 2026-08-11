"""Stdio entry point: ``python -m openscientist_tools``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import mcp.types as types

from openscientist_tools.server import mcp

#: Touched the first time a client asks for the tool inventory. The harness has
#: no way to ask omp which MCP servers it connected to, so the server records the
#: handshake itself and OmpAgent reads the marker (openscientist-io#263).
HANDSHAKE_MARKER = "mcp_handshake"


def _install_handshake_marker() -> None:
    """Record that a client requested our tools, by wrapping the live handler.

    The wrap has to go on ``request_handlers``: ``FastMCP`` binds its own
    ``list_tools`` into that table at construction, so replacing the attribute
    afterwards leaves the registered handler untouched and the marker unwritten.
    """
    job_dir = os.environ.get("OPENSCIENTIST_JOB_DIR")
    if not job_dir:
        return
    marker = Path(job_dir) / ".omp" / HANDSHAKE_MARKER
    handlers = mcp._mcp_server.request_handlers
    original = handlers.get(types.ListToolsRequest)
    if original is None:
        return

    async def record_then_list(*args: Any, **kwargs: Any) -> Any:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        except OSError:
            # Diagnostics only; never fail a tools/list over the marker.
            pass
        return await original(*args, **kwargs)

    handlers[types.ListToolsRequest] = record_then_list


if __name__ == "__main__":
    _install_handshake_marker()
    mcp.run()
