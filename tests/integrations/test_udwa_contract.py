from __future__ import annotations

from importlib import metadata
from unittest.mock import patch

import pytest

from openscientist.integrations.dvc.execution import OPERATION_CONTRACTS
from openscientist.integrations.udwa import (
    PINNED_UDWA_COMMIT,
    REQUIRED_UDWA_OPERATIONS,
    UdwaCompatibilityError,
    inspect_udwa_compatibility,
    require_compatible_udwa,
)


def test_udwa_commit_is_immutable_sha() -> None:
    assert len(PINNED_UDWA_COMMIT) == 40
    int(PINNED_UDWA_COMMIT, 16)


def test_required_operations_cover_first_poc_boundary() -> None:
    assert REQUIRED_UDWA_OPERATIONS == {
        "check_data_sanity",
        "summarize_time_bins",
        "summarize_light_dark",
        "summarize_circadian_cosinor",
    }
    assert set(OPERATION_CONTRACTS) == REQUIRED_UDWA_OPERATIONS


def test_compatibility_report_fails_closed_when_udwa_is_absent() -> None:
    with (
        patch(
            "openscientist.integrations.udwa.metadata.version",
            side_effect=metadata.PackageNotFoundError,
        ),
        patch(
            "openscientist.integrations.udwa._resolve_symbol",
            side_effect=ImportError("not installed"),
        ),
    ):
        report = inspect_udwa_compatibility()
        assert not report.compatible
        assert report.distribution_version == "not-installed"
        with pytest.raises(UdwaCompatibilityError, match="not installed"):
            require_compatible_udwa()


def test_compatibility_report_accepts_required_registry() -> None:
    registry = {name: object() for name in REQUIRED_UDWA_OPERATIONS}

    def resolve(reference: str):
        if reference.endswith(":TOOL_REGISTRY"):
            return registry
        return object()

    with (
        patch("openscientist.integrations.udwa.metadata.version", return_value="0.1.0"),
        patch("openscientist.integrations.udwa._resolve_symbol", side_effect=resolve),
    ):
        report = require_compatible_udwa()

    assert report.compatible
    assert report.distribution_version == "0.1.0"
