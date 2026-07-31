"""Trusted DVC acquisition gateway hosted by the web process.

Only this process resolves DVC connection credentials and imports data. The
agent supplies a logical connection id and a short-lived job capability; all
filesystem destinations are derived server-side from the authenticated job.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from openscientist.dvc_gateway_client import (
    DVC_CAPABILITY_HEADER,
    DVC_GATEWAY_PORT,
)
from openscientist.integrations.dvc.credentials import DVCConnectionNotFoundError
from openscientist.integrations.dvc.models import DVCImportRequest
from openscientist.integrations.dvc.security import redact_sensitive_data, redact_sensitive_text
from openscientist.integrations.dvc.service import DVCAcquisitionError, DVCAcquisitionService
from openscientist.job_container.secrets import verify_dvc_capability
from openscientist.settings import get_settings

logger = logging.getLogger(__name__)

_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_OPERATIONS = frozenset({"test_connection", "list_metrics", "search_cages", "import_dataset"})
_MAX_DISCOVERY_ITEMS = 500
_MAX_SEARCH_PATTERNS = 20
_MAX_CAGES_PER_IMPORT = 20
_MAX_IMPORT_WINDOW = timedelta(days=31)
_MAX_RESPONSE_BYTES = 256 * 1024

ServiceFactory = Callable[[Path], DVCAcquisitionService]


def _error(status: int, code: str, message: str, *, retryable: bool = False) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": redact_sensitive_text(message),
                "retryable": retryable,
            },
        },
        status_code=status,
    )


def _job_dir(job_id: str) -> Path:
    if not _JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid job id.")
    jobs_root = (Path(get_settings().container.container_app_dir) / "jobs").resolve()
    job_dir = (jobs_root / job_id).resolve()
    if jobs_root not in job_dir.parents:
        raise ValueError("Invalid job path.")
    return job_dir


def _exact_arguments(arguments: dict[str, Any], allowed: set[str]) -> None:
    extras = set(arguments) - allowed
    if extras:
        raise ValueError(f"Unsupported arguments: {', '.join(sorted(extras))}.")


def _connection_id(value: object) -> str:
    connection_id = str(value).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", connection_id):
        raise ValueError(
            "connection_id must be 1-100 letters, numbers, dots, dashes or underscores."
        )
    return connection_id


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include an explicit timezone offset.")
    return parsed


def _run_operation(
    service: DVCAcquisitionService,
    operation: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Validate and dispatch one deliberately small operation surface."""

    if operation == "test_connection":
        _exact_arguments(arguments, {"connection_id"})
        result = service.test_connection(_connection_id(arguments.get("connection_id", "default")))
    elif operation == "list_metrics":
        _exact_arguments(arguments, {"connection_id"})
        connection_id = _connection_id(arguments.get("connection_id", "default"))
        metrics = service.list_metrics(connection_id)
        if len(metrics) > _MAX_DISCOVERY_ITEMS:
            raise ValueError(
                f"DVC returned {len(metrics)} metrics; the gateway limit is "
                f"{_MAX_DISCOVERY_ITEMS}. Narrow the configured connection."
            )
        result = {"connection_id": connection_id, "metrics": metrics}
    elif operation == "search_cages":
        _exact_arguments(arguments, {"connection_id", "patterns"})
        patterns = arguments.get("patterns")
        if not isinstance(patterns, list):
            raise ValueError("patterns must be a list.")
        if len(patterns) > _MAX_SEARCH_PATTERNS:
            raise ValueError(f"At most {_MAX_SEARCH_PATTERNS} cage search patterns are allowed.")
        if any(not isinstance(item, str) or not 1 <= len(item.strip()) <= 100 for item in patterns):
            raise ValueError("Each cage search pattern must be a non-empty string up to 100 chars.")
        connection_id = _connection_id(arguments.get("connection_id", "default"))
        cages = service.search_cages(connection_id, patterns)
        if len(cages) > _MAX_DISCOVERY_ITEMS:
            raise ValueError(
                f"DVC returned {len(cages)} cages; refine the search to at most "
                f"{_MAX_DISCOVERY_ITEMS} results."
            )
        result = {"connection_id": connection_id, "cages": cages}
    elif operation == "import_dataset":
        request = DVCImportRequest.model_validate(arguments)
        _connection_id(request.connection_id)
        if len(request.cage_ids) > _MAX_CAGES_PER_IMPORT:
            raise ValueError(f"At most {_MAX_CAGES_PER_IMPORT} cages are allowed per import.")
        start = _parse_timestamp(request.start, "start")
        stop = _parse_timestamp(request.stop, "stop")
        if stop - start > _MAX_IMPORT_WINDOW:
            raise ValueError("DVC import window cannot exceed 31 days.")
        result = service.import_dataset(request).model_dump(mode="json")
    else:
        raise ValueError(f"Unsupported DVC operation {operation!r}.")

    safe = redact_sensitive_data(result)
    encoded = json.dumps(safe, separators=(",", ":"), default=str).encode()
    if len(encoded) > _MAX_RESPONSE_BYTES:
        raise ValueError(
            "DVC result exceeds the gateway response limit; narrow the requested scope."
        )
    return dict(safe)


def create_dvc_gateway_app(
    *,
    master_key: Callable[[], str],
    service_factory: ServiceFactory = DVCAcquisitionService,
) -> Starlette:
    """Build the internal gateway app with injectable dependencies for tests."""

    active_jobs: set[str] = set()

    async def handler(request: Request) -> Response:
        try:
            raw: Any = await request.json()
        except Exception:
            return _error(400, "invalid_request", "Request body must be JSON.")
        if not isinstance(raw, dict):
            return _error(400, "invalid_request", "Request body must be a JSON object.")

        job_id = raw.get("job_id")
        operation = raw.get("operation")
        arguments = raw.get("arguments")
        if not isinstance(job_id, str) or not isinstance(operation, str):
            return _error(400, "invalid_request", "job_id and operation are required.")
        if not isinstance(arguments, dict):
            return _error(400, "invalid_request", "arguments must be a JSON object.")
        if operation not in _OPERATIONS:
            return _error(403, "operation_not_allowed", "DVC operation is not allowed.")

        capability = request.headers.get(DVC_CAPABILITY_HEADER, "")
        if not verify_dvc_capability(master_key(), capability, expected_job_id=job_id):
            return _error(401, "invalid_capability", "DVC capability is missing or expired.")

        try:
            job_dir = _job_dir(job_id)
            # The mounted job must already exist. Never let a capability create
            # an arbitrary job-shaped directory.
            if not job_dir.is_dir():
                return _error(404, "job_not_found", "Authenticated job directory was not found.")
            if job_id in active_jobs:
                return _error(
                    409,
                    "job_busy",
                    "Another DVC acquisition request is already running for this job.",
                    retryable=True,
                )
            active_jobs.add(job_id)
            service = service_factory(job_dir)
            try:
                result = await asyncio.to_thread(_run_operation, service, operation, arguments)
            finally:
                active_jobs.discard(job_id)
        except (ValidationError, ValueError) as exc:
            return _error(400, "invalid_request", str(exc))
        except DVCConnectionNotFoundError as exc:
            return _error(404, "connection_not_found", str(exc))
        except DVCAcquisitionError as exc:
            return _error(502, "upstream_dvc_error", str(exc), retryable=True)
        except Exception:
            logger.exception("Unexpected DVC gateway failure for job %s", job_id)
            return _error(
                500,
                "internal_gateway_error",
                "DVC gateway failed without exposing upstream details.",
                retryable=True,
            )
        return JSONResponse({"ok": True, "result": result})

    return Starlette(routes=[Route("/v1/acquire", handler, methods=["POST"])])


class _NoSignalServer(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        return None


_gateway_server: uvicorn.Server | None = None
_gateway_task: asyncio.Task[None] | None = None


async def start_dvc_gateway() -> None:
    """Start the trusted gateway as a sibling listener in the web process."""

    global _gateway_server, _gateway_task
    if _gateway_task is not None:
        return
    app = create_dvc_gateway_app(master_key=lambda: get_settings().secret_key)
    config = uvicorn.Config(app, host="0.0.0.0", port=DVC_GATEWAY_PORT, log_level="warning")
    _gateway_server = _NoSignalServer(config)
    _gateway_task = asyncio.create_task(_gateway_server.serve())
    logger.info("DVC acquisition gateway listening on port %d", DVC_GATEWAY_PORT)
