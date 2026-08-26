"""Contract-driven trusted gateway for registered preclinical assays."""

from __future__ import annotations

import asyncio
import importlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from openscientist.assays import AssayAdapter, AssayRegistry, GatewayAction, get_assay_registry
from openscientist.assays.security import redact_sensitive_data, redact_sensitive_text
from openscientist.job_container.secrets import verify_assay_capability
from openscientist.settings import get_settings

ASSAY_CAPABILITY_HEADER = "x-openscientist-assay-capability"
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MAX_RESPONSE_BYTES = 256 * 1024

ServiceFactory = Callable[[AssayAdapter, GatewayAction, Path], object]
CapabilityVerifier = Callable[[str, str, str, str], bool]
ResultTransformer = Callable[[str, dict[str, Any], Any], Any]
ErrorMapper = Callable[[Exception], tuple[int, str, bool] | None]
JobDirectoryResolver = Callable[[str], Path]


def _import_symbol(path: str) -> object:
    module_name, separator, attribute = path.rpartition(".")
    if not separator:
        raise ValueError(f"Invalid import path: {path!r}.")
    return getattr(importlib.import_module(module_name), attribute)


def _default_service_factory(
    _adapter: AssayAdapter,
    action: GatewayAction,
    job_dir: Path,
) -> object:
    factory = _import_symbol(action.handler_path)
    if not callable(factory):
        raise TypeError(f"Assay gateway handler is not callable: {action.handler_path}.")
    return factory(job_dir)


def _action(adapter: AssayAdapter, action_name: str) -> GatewayAction:
    matches = [item for item in adapter.gateway_actions if item.action == action_name]
    if len(matches) != 1:
        raise KeyError(action_name)
    return matches[0]


def dispatch_assay_action(
    *,
    adapter: AssayAdapter,
    action: GatewayAction,
    job_dir: Path,
    arguments: Mapping[str, Any],
    service_factory: ServiceFactory = _default_service_factory,
) -> Any:
    """Validate raw arguments from the untrusted process before dispatch."""

    request_model = _import_symbol(action.request_model_path)
    if not isinstance(request_model, type) or not issubclass(request_model, BaseModel):
        raise TypeError(f"Invalid request model: {action.request_model_path}.")
    request = request_model.model_validate(dict(arguments))
    service = service_factory(adapter, action, job_dir)
    method = getattr(service, action.service_method, None)
    if not callable(method):
        raise TypeError(
            f"Assay handler {action.handler_path} has no callable {action.service_method!r}."
        )
    if action.invocation == "kwargs":
        result = method(**request.model_dump(mode="python"))
    else:
        result = method(request)
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if is_dataclass(result) and not isinstance(result, type):
        return asdict(result)
    return result


def _job_dir(job_id: str) -> Path:
    if not _JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid job id.")
    jobs_root = (Path(get_settings().container.container_app_dir) / "jobs").resolve()
    job_dir = (jobs_root / job_id).resolve()
    if jobs_root not in job_dir.parents:
        raise ValueError("Invalid job path.")
    return job_dir


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


def create_assay_gateway_app(
    *,
    master_key: Callable[[], str],
    registry: AssayRegistry | None = None,
    service_factory: ServiceFactory = _default_service_factory,
    capability_header: str | tuple[str, ...] = ASSAY_CAPABILITY_HEADER,
    capability_verifier: CapabilityVerifier | None = None,
    result_transformer: ResultTransformer | None = None,
    error_mapper: ErrorMapper | None = None,
    legacy_assay_id: str | None = None,
    job_dir_resolver: JobDirectoryResolver = _job_dir,
) -> Starlette:
    """Build a gateway whose permissions and handlers come only from adapters."""

    assay_registry = registry or get_assay_registry()
    active_runs: set[tuple[str, str, str]] = set()

    def verify(token: str, job_id: str, assay_id: str, permission: str) -> bool:
        if capability_verifier is not None:
            return capability_verifier(token, job_id, assay_id, permission)
        return verify_assay_capability(
            master_key(),
            token,
            expected_job_id=job_id,
            expected_assay_id=assay_id,
            required_permission=permission,
        )

    async def handler(request: Request) -> Response:
        try:
            raw: Any = await request.json()
        except Exception:
            return _error(400, "invalid_request", "Request body must be JSON.")
        if not isinstance(raw, dict):
            return _error(400, "invalid_request", "Request body must be a JSON object.")

        assay_id = request.path_params.get("assay_id") or legacy_assay_id
        job_id = raw.get("job_id")
        action_name = raw.get("action", raw.get("operation"))
        arguments = raw.get("arguments")
        if not isinstance(assay_id, str) or not isinstance(job_id, str):
            return _error(400, "invalid_request", "assay_id and job_id are required.")
        if not isinstance(action_name, str) or not isinstance(arguments, dict):
            return _error(400, "invalid_request", "action and arguments are required.")
        try:
            adapter = assay_registry.require(assay_id)
            action = _action(adapter, action_name)
        except KeyError:
            return _error(403, "action_not_allowed", "Assay action is not allowed.")
        except Exception as exc:
            return _error(404, "assay_not_registered", str(exc))

        capability_headers = (
            (capability_header,) if isinstance(capability_header, str) else capability_header
        )
        token = next(
            (value for header in capability_headers if (value := request.headers.get(header, ""))),
            "",
        )
        if not verify(token, job_id, assay_id, action.permission):
            return _error(401, "invalid_capability", "Assay capability is missing or expired.")

        # Serialize retries of one analysis identity without preventing a study
        # from running independent datasets/operations concurrently.
        run_scope = str(
            arguments.get("run_id")
            or ":".join(
                str(value)
                for value in (arguments.get("dataset_id"), arguments.get("operation"))
                if value is not None
            )
            or action_name
        )
        active_key = (job_id, assay_id, run_scope)
        try:
            job_dir = job_dir_resolver(job_id)
            if not job_dir.is_dir():
                return _error(404, "job_not_found", "Authenticated job directory was not found.")
            if active_key in active_runs:
                return _error(
                    409,
                    "assay_busy",
                    "Another assay gateway request is already running for this job and assay.",
                    retryable=True,
                )
            active_runs.add(active_key)
            try:
                result = await asyncio.to_thread(
                    dispatch_assay_action,
                    adapter=adapter,
                    action=action,
                    job_dir=job_dir,
                    arguments=arguments,
                    service_factory=service_factory,
                )
                if result_transformer is not None:
                    result = result_transformer(action_name, arguments, result)
            finally:
                active_runs.discard(active_key)
            safe_result = redact_sensitive_data(result)
            encoded = json.dumps(safe_result, separators=(",", ":"), default=str).encode()
            if len(encoded) > _MAX_RESPONSE_BYTES:
                raise ValueError("Assay result exceeds the gateway response limit.")
        except (ValidationError, ValueError, TypeError) as exc:
            return _error(400, "invalid_request", str(exc))
        except Exception as exc:
            mapped = error_mapper(exc) if error_mapper is not None else None
            if mapped is None:
                return _error(
                    500,
                    "internal_gateway_error",
                    "Assay gateway failed without exposing upstream details.",
                    retryable=True,
                )
            status, code, retryable = mapped
            return _error(status, code, str(exc), retryable=retryable)
        return JSONResponse({"ok": True, "result": safe_result})

    routes = [Route("/v1/assays/{assay_id}/acquire", handler, methods=["POST"])]
    if legacy_assay_id is not None:
        routes.append(Route("/v1/acquire", handler, methods=["POST"]))
    return Starlette(routes=routes)
