"""Stable OpenScientist boundary to the pinned UDWA package.

UDWA owns DVC acquisition, normalization, and deterministic calculations.
OpenScientist owns scientific prerequisites, approvals, provenance, and reporting.
This module checks that the installed UDWA distribution exposes the small public
surface required by the integration before any DVC job is allowed to run.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module, metadata
from typing import Any

PINNED_UDWA_COMMIT = "2a7f8ff042f2db1baa6e368126cbc4bd9034bd88"
UDWA_DISTRIBUTION = "udwa"

REQUIRED_UDWA_IMPORTS = (
    "udwa.ingest:DvcApiClient",
    "udwa.ingest:fetch_api_bundle",
    "udwa.ingest:get_metrics_list",
    "udwa.ingest:search_cages_list",
    "udwa.ingest:test_api_connection",
    "udwa.orchestrator:TOOL_REGISTRY",
    "udwa.orchestrator:list_tools",
    "udwa.orchestrator:run_tool",
)

# Deliberately narrow first POC set. Adding an operation requires an explicit
# scientific contract in OpenScientist rather than merely discovering a new
# function in UDWA.
REQUIRED_UDWA_OPERATIONS = frozenset(
    {
        "check_data_sanity",
        "summarize_time_bins",
        "summarize_light_dark",
        "summarize_circadian_cosinor",
    }
)


class UdwaCompatibilityError(RuntimeError):
    """The installed UDWA package cannot satisfy the OpenScientist contract."""


@dataclass(frozen=True)
class UdwaCompatibilityReport:
    distribution_version: str
    pinned_commit: str
    missing_imports: tuple[str, ...]
    missing_operations: tuple[str, ...]

    @property
    def compatible(self) -> bool:
        return not self.missing_imports and not self.missing_operations


def _resolve_symbol(reference: str) -> Any:
    module_name, symbol_name = reference.split(":", 1)
    module = import_module(module_name)
    return getattr(module, symbol_name)


def inspect_udwa_compatibility() -> UdwaCompatibilityReport:
    """Inspect imports and registry entries without running scientific tools."""

    try:
        version = metadata.version(UDWA_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        version = "not-installed"

    missing_imports: list[str] = []
    resolved: dict[str, Any] = {}
    for reference in REQUIRED_UDWA_IMPORTS:
        try:
            resolved[reference] = _resolve_symbol(reference)
        except (ImportError, AttributeError, ValueError):
            missing_imports.append(reference)

    registry = resolved.get("udwa.orchestrator:TOOL_REGISTRY")
    available_operations = set(registry) if isinstance(registry, dict) else set()
    missing_operations = sorted(REQUIRED_UDWA_OPERATIONS - available_operations)

    return UdwaCompatibilityReport(
        distribution_version=version,
        pinned_commit=PINNED_UDWA_COMMIT,
        missing_imports=tuple(sorted(missing_imports)),
        missing_operations=tuple(missing_operations),
    )


def require_compatible_udwa() -> UdwaCompatibilityReport:
    """Return the compatibility report or fail closed with actionable detail."""

    report = inspect_udwa_compatibility()
    if report.compatible:
        return report

    details: list[str] = []
    if report.distribution_version == "not-installed":
        details.append("the pinned UDWA dependency is not installed")
    if report.missing_imports:
        details.append("missing imports: " + ", ".join(report.missing_imports))
    if report.missing_operations:
        details.append("missing operations: " + ", ".join(report.missing_operations))
    raise UdwaCompatibilityError("UDWA compatibility check failed: " + "; ".join(details))
