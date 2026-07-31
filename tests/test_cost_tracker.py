"""Tests for application-recorded cost aggregation."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openscientist.cost_tracker import get_recorded_cost_info
from openscientist.providers.base import Provider


@pytest.mark.asyncio
async def test_get_recorded_cost_info_aggregates_provider_spend() -> None:
    result = MagicMock()
    result.one.return_value = (12.5, 2.25, 3)
    session = AsyncMock()
    session.execute.return_value = result
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    provider = cast(Provider, SimpleNamespace(id="openai", display_name="OpenAI API"))

    with patch("openscientist.cost_tracker.AsyncSessionLocal", return_value=session_context):
        info = await get_recorded_cost_info(provider, lookback_hours=48)

    assert info.total_spend_usd == 12.5
    assert info.recent_spend_usd == 2.25
    assert info.recent_period_hours == 48
    assert info.metadata == {
        "source": "openscientist_cost_records",
        "estimated": True,
        "record_count": 3,
    }
    assert "outside this app" in (info.data_lag_note or "")
    session.execute.assert_awaited_once()
