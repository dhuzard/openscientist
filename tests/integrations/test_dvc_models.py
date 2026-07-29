from __future__ import annotations

import pytest
from pydantic import ValidationError

from openscientist.integrations.dvc.models import DVCImportRequest


def test_import_request_normalizes_metric() -> None:
    request = DVCImportRequest(
        connection_id="default",
        cage_ids=["S81P-40332"],
        metric_id="edge",
        start="2025-12-05",
        stop="2025-12-08",
        aggregation="MINUTE",
    )

    assert request.metric_id == "EDGE"


@pytest.mark.parametrize("metric", ["EDGE,AVERAGE", "EDGE AVERAGE"])
def test_import_request_rejects_multiple_metrics(metric: str) -> None:
    with pytest.raises(ValidationError):
        DVCImportRequest(
            connection_id="default",
            cage_ids=["S81P-40332"],
            metric_id=metric,
            start="2025-12-05",
            stop="2025-12-08",
        )


def test_import_request_rejects_invalid_window() -> None:
    with pytest.raises(ValidationError):
        DVCImportRequest(
            connection_id="default",
            cage_ids=["S81P-40332"],
            metric_id="EDGE",
            start="2025-12-08",
            stop="2025-12-05",
        )


def test_import_request_rejects_duplicate_cages() -> None:
    with pytest.raises(ValidationError):
        DVCImportRequest(
            connection_id="default",
            cage_ids=["S81P-40332", "S81P-40332"],
            metric_id="EDGE",
            start="2025-12-05",
            stop="2025-12-08",
        )
