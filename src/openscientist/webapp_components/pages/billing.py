"""Billing and cost tracking panel."""

import logging

from nicegui import ui
from sqlalchemy import select

from openscientist.database.models import CostRecord
from openscientist.database.session import get_admin_session
from openscientist.providers import get_provider
from openscientist.usage_summary import (
    UsageAggregate,
    aggregate_model_usage,
    aggregate_operation_usage,
    summarize_usage,
)
from openscientist.webapp_components.ui_components import render_empty_state, render_stat_badges
from openscientist.webapp_components.utils.client_guard import guard_client, setup_timer_cleanup

logger = logging.getLogger(__name__)


async def render_billing_panel() -> None:
    """Render the billing panel (for use as an admin subtab)."""
    active_timers = setup_timer_cleanup()

    with ui.column().classes("w-full gap-6"):

        @ui.refreshable
        def render_billing_snapshot(cost_records: list[CostRecord]) -> None:
            _render_db_cost_records(cost_records)
            _render_provider_cost_section()

        render_billing_snapshot(await _load_cost_records())

        @guard_client
        async def refresh_billing_snapshot() -> None:
            render_billing_snapshot.refresh(await _load_cost_records())

        active_timers.append(ui.timer(5.0, refresh_billing_snapshot))


async def _load_cost_records() -> list[CostRecord]:
    """Load cost rows for the live billing summary."""
    async with get_admin_session() as session:
        records = await session.execute(select(CostRecord).order_by(CostRecord.created_at.desc()))
        return list(records.scalars().all())


async def _render_db_cost_section() -> None:
    """Query CostRecord and display per-turn cost breakdown."""
    _render_db_cost_records(await _load_cost_records())


def _render_db_cost_records(cost_records: list[CostRecord]) -> None:
    """Display a snapshot of recorded costs."""
    ui.label("Live estimates refresh every 5 seconds after each completed agent turn.").classes(
        "text-sm text-grey-6"
    )

    if not cost_records:
        render_empty_state(
            "No cost records yet. Costs appear after the first agent turn completes."
        )
        return

    totals = summarize_usage(cost_records)

    render_stat_badges(
        [
            ("Total Cost", f"${totals.cost_usd:.4f}", "green"),
            ("Fresh Input", f"{totals.input_tokens:,}", "blue"),
            ("Visible Output", f"{totals.output_tokens:,}", "orange"),
            ("Cache Read", f"{totals.cache_read_tokens:,}", "teal"),
            ("Cache Write", f"{totals.cache_write_tokens:,}", "cyan"),
            ("Reasoning", f"{totals.reasoning_tokens:,}", "deep-purple"),
            ("Total Tokens", f"{totals.total_tokens:,}", "indigo"),
            ("Model Turns", f"{totals.turn_count:,}", "purple"),
            ("Models", f"{totals.model_count:,}", "cyan"),
        ]
    )

    _render_aggregate_table(
        "Usage by Model",
        aggregate_model_usage(cost_records),
        include_operation=False,
        show_job_count=True,
    )
    _render_aggregate_table(
        "Usage by Task / Operation and Model",
        aggregate_operation_usage(cost_records),
        include_operation=True,
        show_job_count=True,
    )

    ui.label("Completed Model Turns").classes("text-h6 font-bold mt-2")
    ui.label(
        "Each row is one completed agent turn. Report and consensus turns do not have "
        "an investigation iteration number."
    ).classes("text-sm text-grey-6")
    columns = [
        {"name": "job_id", "label": "Job", "field": "job_id", "align": "left"},
        {"name": "operation", "label": "Task / Operation", "field": "operation", "align": "left"},
        {"name": "iteration", "label": "Iteration", "field": "iteration", "align": "right"},
        {"name": "model", "label": "Model", "field": "model", "align": "left"},
        {"name": "provider", "label": "Provider", "field": "provider", "align": "left"},
        {
            "name": "input_tokens",
            "label": "Fresh Input",
            "field": "input_tokens",
            "align": "right",
        },
        {
            "name": "output_tokens",
            "label": "Visible Output",
            "field": "output_tokens",
            "align": "right",
        },
        {
            "name": "cache_read_tokens",
            "label": "Cache Read",
            "field": "cache_read_tokens",
            "align": "right",
        },
        {
            "name": "cache_write_tokens",
            "label": "Cache Write",
            "field": "cache_write_tokens",
            "align": "right",
        },
        {
            "name": "reasoning_tokens",
            "label": "Reasoning",
            "field": "reasoning_tokens",
            "align": "right",
        },
        {"name": "cost_usd", "label": "Cost (USD)", "field": "cost_usd", "align": "right"},
        {"name": "created_at", "label": "Date", "field": "created_at", "align": "left"},
    ]
    rows = [
        {
            "id": str(r.id),
            "job_id": "..." + str(r.job_id)[-8:],
            "operation": r.operation_type or "Unspecified",
            "iteration": r.iteration if r.iteration is not None else "—",
            "model": r.model or "Unknown model",
            "provider": r.provider or "Unknown provider",
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "cache_read_tokens": r.cache_read_tokens,
            "cache_write_tokens": r.cache_write_tokens,
            "reasoning_tokens": r.reasoning_tokens,
            "cost_usd": f"${r.cost_usd:.4f}",
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for r in cost_records
    ]
    ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")

    if any(not r.model or r.model.casefold() == "unknown" for r in cost_records):
        with ui.card().classes("w-full bg-yellow-50 border border-yellow-300 p-3"):
            ui.label(
                "Some turns have an unknown model. Their token counts are real, but their "
                "estimated dollar cost may be zero or incomplete because no model price matched."
            ).classes("text-sm text-yellow-800")


def _render_aggregate_table(
    title: str,
    aggregates: list[UsageAggregate],
    *,
    include_operation: bool,
    show_job_count: bool,
) -> None:
    """Render a reusable model/task usage table."""
    ui.label(title).classes("text-h6 font-bold mt-2")
    columns = []
    if include_operation:
        columns.append(
            {
                "name": "operation",
                "label": "Task / Operation",
                "field": "operation",
                "align": "left",
            }
        )
    columns.extend(
        [
            {"name": "model", "label": "Model", "field": "model", "align": "left"},
            {"name": "provider", "label": "Provider", "field": "provider", "align": "left"},
        ]
    )
    if show_job_count:
        columns.append({"name": "jobs", "label": "Jobs", "field": "jobs", "align": "right"})
    columns.extend(
        [
            {"name": "turns", "label": "Turns", "field": "turns", "align": "right"},
            {
                "name": "iterations",
                "label": "Iterations",
                "field": "iterations",
                "align": "right",
            },
            {
                "name": "input_tokens",
                "label": "Fresh Input",
                "field": "input_tokens",
                "align": "right",
            },
            {
                "name": "output_tokens",
                "label": "Visible Output",
                "field": "output_tokens",
                "align": "right",
            },
            {
                "name": "cache_read_tokens",
                "label": "Cache Read",
                "field": "cache_read_tokens",
                "align": "right",
            },
            {
                "name": "cache_write_tokens",
                "label": "Cache Write",
                "field": "cache_write_tokens",
                "align": "right",
            },
            {
                "name": "reasoning_tokens",
                "label": "Reasoning",
                "field": "reasoning_tokens",
                "align": "right",
            },
            {
                "name": "total_tokens",
                "label": "Total Tokens",
                "field": "total_tokens",
                "align": "right",
            },
            {"name": "cost", "label": "Cost (USD)", "field": "cost", "align": "right"},
        ]
    )
    rows = [
        {
            "id": f"{index}:{aggregate.provider}:{aggregate.model}:{aggregate.operation_type}",
            "operation": aggregate.operation_type or "All tasks",
            "model": aggregate.model,
            "provider": aggregate.provider,
            "jobs": aggregate.job_count,
            "turns": aggregate.turn_count,
            "iterations": aggregate.iteration_label,
            "input_tokens": f"{aggregate.input_tokens:,}",
            "output_tokens": f"{aggregate.output_tokens:,}",
            "cache_read_tokens": f"{aggregate.cache_read_tokens:,}",
            "cache_write_tokens": f"{aggregate.cache_write_tokens:,}",
            "reasoning_tokens": f"{aggregate.reasoning_tokens:,}",
            "total_tokens": f"{aggregate.total_tokens:,}",
            "cost": f"${aggregate.cost_usd:.4f}",
        }
        for index, aggregate in enumerate(aggregates)
    ]
    ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")


def _render_provider_cost_section() -> None:
    """Provider-level cost API."""
    with ui.card().classes("w-full max-w-4xl"):
        ui.label("Provider Cost API").classes("text-h6 mb-4")

        try:
            provider = get_provider()
            cost_info = provider.get_budget_cost_info(lookback_hours=24)
            budget_check = provider.evaluate_budget(cost_info)

            with ui.row().classes("w-full gap-8 mb-4"):
                # Total spend
                with ui.card().classes("flex-1"):
                    total_spend_display = (
                        f"${cost_info.total_spend_usd:.2f}"
                        if cost_info.total_spend_usd is not None
                        else "N/A"
                    )
                    ui.label(total_spend_display).classes("text-h3 text-primary")
                    ui.label("Total Spend").classes("text-subtitle2 text-grey")

                # Last 24h
                with ui.card().classes("flex-1"):
                    recent_spend_display = (
                        f"${cost_info.recent_spend_usd:.2f}"
                        if cost_info.recent_spend_usd is not None
                        else "N/A"
                    )
                    ui.label(recent_spend_display).classes("text-h3")
                    ui.label(f"Last {cost_info.recent_period_hours} Hours").classes(
                        "text-subtitle2 text-grey"
                    )

                # Budget remaining (if available)
                if cost_info.budget_remaining_usd is not None:
                    with ui.card().classes("flex-1"):
                        remaining_color = (
                            "text-positive"
                            if cost_info.budget_remaining_usd > 10
                            else "text-warning"
                        )
                        ui.label(f"${cost_info.budget_remaining_usd:.2f}").classes(
                            f"text-h3 {remaining_color}"
                        )
                        ui.label("Budget Remaining").classes("text-subtitle2 text-grey")

            # Provider info
            with ui.card().classes("w-full bg-gray-50"):
                ui.label("Provider Information").classes("text-subtitle2 font-bold mb-2")
                ui.label(f"Provider: {cost_info.provider_name}").classes("text-sm")
                if cost_info.data_lag_note:
                    ui.label(cost_info.data_lag_note).classes("text-sm text-grey-6")

            # Budget warnings/errors
            if budget_check and budget_check.get("errors"):
                with ui.card().classes("w-full bg-red-50 border border-red-300 mt-4 p-4"):
                    ui.label("Budget Alerts").classes("text-subtitle2 font-bold text-red-800 mb-2")
                    for error in budget_check["errors"]:
                        ui.label(f"Warning: {error}").classes("text-sm text-red-600")
            elif budget_check and budget_check.get("warnings"):
                with ui.card().classes("w-full bg-yellow-50 border border-yellow-300 mt-4 p-4"):
                    ui.label("Budget Warnings").classes(
                        "text-subtitle2 font-bold text-yellow-800 mb-2"
                    )
                    for warning in budget_check["warnings"]:
                        ui.label(f"Warning: {warning}").classes("text-sm text-yellow-600")

        except Exception as e:
            logger.warning("Cost tracking unavailable: %s", e)
            with ui.card().classes("w-full bg-yellow-50 border border-yellow-300 p-4"):
                ui.label("Cost Tracking Unavailable").classes(
                    "text-subtitle2 font-bold text-yellow-800 mb-2"
                )
                ui.label("Check provider configuration in .env").classes("text-sm text-gray-600")
