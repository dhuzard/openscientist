"""Application-level cost aggregation and budget reporting.

Some providers do not expose cost data for the credential used to run jobs.
For those providers, OpenScientist can still enforce its own budgets from the
``cost_records`` ledger populated when jobs complete.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, func, select

from openscientist.async_tasks import run_sync
from openscientist.database.models import CostRecord
from openscientist.database.session import AsyncSessionLocal
from openscientist.providers.base import CostInfo

if TYPE_CHECKING:
    from openscientist.providers.base import Provider


async def get_recorded_cost_info(
    provider: "Provider",
    lookback_hours: int = 24,
) -> CostInfo:
    """Aggregate estimated spend recorded by OpenScientist for ``provider``.

    A fresh, unpooled session is used because budget checks are normally
    initiated by synchronous job-management code and may run on a worker
    thread with its own event loop.
    """
    now = datetime.now(UTC)
    recent_start = now - timedelta(hours=lookback_hours)
    provider_names = {provider.id.lower(), provider.display_name.lower()}

    total_cost = func.coalesce(func.sum(CostRecord.cost_usd), 0.0)
    recent_cost = func.coalesce(
        func.sum(case((CostRecord.created_at >= recent_start, CostRecord.cost_usd), else_=0.0)),
        0.0,
    )
    statement = select(total_cost, recent_cost, func.count(CostRecord.id)).where(
        func.lower(CostRecord.provider).in_(provider_names)
    )

    async with AsyncSessionLocal(thread_safe=True) as session:
        result = await session.execute(statement)
        total_spend, recent_spend, record_count = result.one()

    return CostInfo(
        provider_name=provider.display_name,
        total_spend_usd=float(total_spend),
        recent_spend_usd=float(recent_spend),
        recent_period_hours=lookback_hours,
        last_updated=now,
        data_lag_note=(
            "Estimated from completed OpenScientist jobs; OpenAI activity "
            "outside this app is not included."
        ),
        metadata={
            "source": "openscientist_cost_records",
            "estimated": True,
            "record_count": int(record_count),
        },
    )


def get_recorded_cost_info_sync(
    provider: "Provider",
    lookback_hours: int = 24,
) -> CostInfo:
    """Synchronous bridge for :func:`get_recorded_cost_info`."""
    return run_sync(get_recorded_cost_info(provider, lookback_hours))


def get_budget_info(lookback_hours: int = 24) -> dict[str, Any]:
    """Return CLI-friendly cost and budget details for the active provider."""
    from openscientist.providers import get_provider

    provider = get_provider()
    cost_info = provider.get_budget_cost_info(lookback_hours)
    return {
        "cost_info": asdict(cost_info),
        "budget_check": provider.evaluate_budget(cost_info),
    }
