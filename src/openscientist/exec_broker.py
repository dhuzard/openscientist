"""Web-side execution broker: an authenticated internal listener wrapping
ContainerManager.execute_code() so a job container runs code without the
Docker socket. Accepts only execute_code parameters, never a container spec.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from openscientist.container_manager import ContainerManager, get_container_manager
from openscientist.exec_broker_client import EXEC_BROKER_PORT, EXEC_TOKEN_HEADER
from openscientist.job_container.secrets import make_exec_placeholder
from openscientist.job_container.utils import HostPathSettings, to_container_path
from openscientist.settings import get_settings

logger = logging.getLogger(__name__)

_SUPPORTED_LANGUAGES = frozenset({"python", "rust", "sparql"})


class _PathConfinementError(ValueError):
    """A requested path resolves outside the authenticated job's directory."""


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Return the stable broker failure envelope consumed by the UI/policy."""
    return JSONResponse(
        {
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details or {},
            }
        },
        status_code=status_code,
    )


def _job_container_root(cs: HostPathSettings, job_id: str) -> Path:
    """The web-container path of the job's own directory."""
    path = Path(cs.container_app_dir) / "jobs" / job_id
    return path.resolve() if os.name != "nt" else path


def _confined_container_path(host_path: str, cs: HostPathSettings, job_root: Path) -> Path:
    """Map a host path to the web-container path, rejecting anything outside the job dir."""
    container_path = to_container_path(Path(host_path), cs)
    if os.name != "nt":
        container_path = container_path.resolve()
    if container_path != job_root and job_root not in container_path.parents:
        raise _PathConfinementError(f"path {host_path!r} is outside job directory {job_root}")
    return container_path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_job_asset(
    value: Any,
    *,
    cs: HostPathSettings,
    job_root: Path,
    require_file: bool,
) -> Path:
    """Resolve a job-relative asset ref, with legacy host-path compatibility."""
    if isinstance(value, str):
        path = _confined_container_path(value, cs, job_root)
    elif isinstance(value, dict):
        relpath = value.get("job_relpath")
        if not isinstance(relpath, str) or not relpath:
            raise ValueError("asset reference requires job_relpath")
        candidate = Path(relpath)
        if candidate.is_absolute():
            raise _PathConfinementError("job_relpath must be relative")
        path = job_root / candidate
        if os.name != "nt":
            path = path.resolve()
        if path != job_root and job_root not in path.parents:
            raise _PathConfinementError(f"job_relpath {relpath!r} escapes the job directory")
        expected_sha = value.get("sha256")
        asset_id = value.get("asset_id")
        if expected_sha is not None:
            if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
                raise ValueError("asset sha256 must be 64 lowercase hexadecimal characters")
            if not path.is_file() or _sha256(path) != expected_sha:
                raise ValueError(f"asset integrity check failed: {asset_id or relpath}")
        if asset_id is not None and not isinstance(asset_id, str):
            raise ValueError("asset_id must be a string")
    else:
        raise ValueError("path must be a job-relative asset reference")
    if require_file and isinstance(value, dict) and not path.is_file():
        raise ValueError(f"asset file not found: {path.name}")
    return path


def create_exec_broker_app(
    *,
    master_key: Callable[[], str],
    manager: Callable[[], ContainerManager],
) -> Starlette:
    """Build the broker ASGI app. The callables are resolved per request."""

    async def handler(request: Request) -> Response:
        presented = request.headers.get(EXEC_TOKEN_HEADER, "")
        try:
            raw: Any = await request.json()
        except Exception:
            return _error_response(
                status_code=400, code="invalid_json", message="request body must be JSON"
            )
        if not isinstance(raw, dict):
            return _error_response(
                status_code=400,
                code="invalid_request",
                message="request body must be a JSON object",
            )
        body: dict[str, Any] = raw

        job_id = body.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return _error_response(status_code=400, code="missing_job_id", message="missing job_id")

        # Recompute the token from the claimed job_id and constant-time compare.
        expected = make_exec_placeholder(master_key(), job_id)
        if not presented or not hmac.compare_digest(expected, presented):
            return _error_response(status_code=401, code="unauthorized", message="unauthorized")

        language = body.get("language", "python")
        if language not in _SUPPORTED_LANGUAGES:
            return _error_response(
                status_code=400,
                code="unsupported_language",
                message=f"unsupported language {language!r}",
            )
        code = body.get("code")
        if not isinstance(code, str):
            return _error_response(status_code=400, code="missing_code", message="missing code")
        description = body.get("description", "")
        if not isinstance(description, str):
            return _error_response(
                status_code=400,
                code="invalid_description",
                message="description must be a string",
            )
        output_dir_raw = body.get("output_ref", body.get("output_dir"))
        if output_dir_raw is None:
            return _error_response(
                status_code=400, code="missing_output", message="missing output_ref"
            )
        try:
            iteration = int(body.get("iteration", 0))
            timeout_raw = body.get("timeout")
            timeout = int(timeout_raw) if timeout_raw else None
        except (TypeError, ValueError):
            return _error_response(
                status_code=400,
                code="invalid_limits",
                message="iteration and timeout must be integers",
            )

        data_path_raw = body.get("data_ref", body.get("data_path"))
        data_files_raw = body.get("data_files", [])
        if not isinstance(data_files_raw, list):
            return _error_response(
                status_code=400,
                code="invalid_data_files",
                message="data_files must be a list",
            )

        cs = get_settings().container
        job_root = _job_container_root(cs, job_id)
        try:
            output_dir = _resolve_job_asset(
                output_dir_raw, cs=cs, job_root=job_root, require_file=False
            )
            data_path: Path | None = None
            if data_path_raw:
                data_path = _resolve_job_asset(
                    data_path_raw, cs=cs, job_root=job_root, require_file=True
                )
            data_files: list[dict[str, Any]] = []
            for entry in data_files_raw:
                if not isinstance(entry, dict):
                    return _error_response(
                        status_code=400,
                        code="invalid_asset",
                        message="data_files entries must be objects",
                    )
                raw_path = entry.get("asset", entry.get("path", ""))
                if raw_path:
                    confined = _resolve_job_asset(
                        raw_path, cs=cs, job_root=job_root, require_file=True
                    )
                    clean_entry = {key: value for key, value in entry.items() if key != "asset"}
                    data_files.append({**clean_entry, "path": str(confined)})
                else:
                    data_files.append(dict(entry))
        except _PathConfinementError as exc:
            return _error_response(
                status_code=403,
                code="path_outside_job",
                message=str(exc),
            )
        except (OSError, ValueError) as exc:
            return _error_response(
                status_code=422,
                code="invalid_asset",
                message=str(exc),
            )

        # execute_code() blocks on a container, so run it off the event loop.
        try:
            result = await asyncio.to_thread(
                manager().execute_code,
                code=code,
                job_id=job_id,
                data_path=data_path.as_posix() if data_path is not None else None,
                output_dir=output_dir,
                timeout=timeout,
                description=description,
                iteration=iteration,
                data_files=[
                    {**entry, "path": Path(str(entry["path"])).as_posix()}
                    if entry.get("path")
                    else entry
                    for entry in data_files
                ],
                language=language,
            )
        except Exception as exc:
            logger.exception("Execution broker failed for job %s", job_id)
            return _error_response(
                status_code=500,
                code="executor_internal_error",
                message=f"executor service failed: {type(exc).__name__}: {exc}",
                retryable=True,
            )
        return JSONResponse(result)

    return Starlette(routes=[Route("/execute", handler, methods=["POST"])])


class _NoSignalServer(uvicorn.Server):
    """A second server sharing the loop must not install signal handlers."""

    def install_signal_handlers(self) -> None:
        return None


_broker_server: uvicorn.Server | None = None
_broker_task: asyncio.Task[None] | None = None


async def start_exec_broker() -> None:
    """Start the broker listener as a loop task. The single, always-on exec path."""
    global _broker_server, _broker_task
    if _broker_task is not None:
        return
    app = create_exec_broker_app(
        master_key=lambda: get_settings().secret_key,
        manager=get_container_manager,
    )
    config = uvicorn.Config(app, host="0.0.0.0", port=EXEC_BROKER_PORT, log_level="warning")
    _broker_server = _NoSignalServer(config)
    _broker_task = asyncio.create_task(_broker_server.serve())
    logger.info("Execution broker listening on port %d", EXEC_BROKER_PORT)
