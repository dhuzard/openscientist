from __future__ import annotations

import pytest
from pydantic import ValidationError

from openscientist.integrations.dvc.models import DVCImportRequest


def test_import_request_normalizes_metric() -> None:
    request = DVCImportRequest(
        connection_id="default",
        cage_ids=["S81P-40332"],
        metric_id="edge",
        start="2025-12-05T00:00:00Z",
        stop="2025-12-08T00:00:00Z",
        aggregation="MINUTE",
    )

    assert request.metric_id == "EDGE"
    assert request.start == "2025-12-05T00:00:00Z"


@pytest.mark.parametrize("metric", ["EDGE,AVERAGE", "EDGE AVERAGE"])
def test_import_request_rejects_multiple_metrics(metric: str) -> None:
    with pytest.raises(ValidationError):
        DVCImportRequest(
            connection_id="default",
            cage_ids=["S81P-40332"],
            metric_id=metric,
            start="2025-12-05T00:00:00Z",
            stop="2025-12-08T00:00:00Z",
        )


def test_import_request_rejects_invalid_window() -> None:
    with pytest.raises(ValidationError):
        DVCImportRequest(
            connection_id="default",
            cage_ids=["S81P-40332"],
            metric_id="EDGE",
            start="2025-12-08T00:00:00Z",
            stop="2025-12-05T00:00:00Z",
        )


def test_import_request_rejects_duplicate_cages() -> None:
    with pytest.raises(ValidationError):
        DVCImportRequest(
            connection_id="default",
            cage_ids=["S81P-40332", "S81P-40332"],
            metric_id="EDGE",
            start="2025-12-05T00:00:00Z",
            stop="2025-12-08T00:00:00Z",
        )


def test_import_request_rejects_vendor_uuid_as_cage_id() -> None:
    with pytest.raises(ValidationError, match="humanReadableId"):
        DVCImportRequest(
            connection_id="default",
            cage_ids=["96da8b14-7b63-4fa0-9bc8-c8e5a6f1a29c"],
            metric_id="EDGE",
            start="2025-12-05T00:00:00Z",
            stop="2025-12-08T00:00:00Z",
        )


def test_import_request_canonicalizes_equivalent_timezone_bounds() -> None:
    request = DVCImportRequest(
        connection_id="default",
        cage_ids=["S81P-40332"],
        metric_id="EDGE",
        start="2025-12-05T01:00:00+01:00",
        stop="2025-12-08T01:00:00+01:00",
    )

    assert request.start == "2025-12-05T00:00:00Z"
    assert request.stop == "2025-12-08T00:00:00Z"


def test_import_request_rejects_bounds_without_timezone() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        DVCImportRequest(
            connection_id="default",
            cage_ids=["S81P-40332"],
            metric_id="EDGE",
            start="2025-12-05T00:00:00",
            stop="2025-12-08T00:00:00",
        )
