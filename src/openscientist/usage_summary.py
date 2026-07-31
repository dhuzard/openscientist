"""Shared aggregation helpers for transparent agent token and cost reporting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


def _display_value(value: Any, fallback: str) -> str:
    """Return a stable, explicit label for optional provider metadata."""
    text = str(value or "").strip()
    return text or fallback


@dataclass(frozen=True)
class UsageTotals:
    """Totals across a collection of completed model turns."""

    turn_count: int
    job_count: int
    model_count: int
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    reasoning_tokens: int
    cost_usd: float

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_write_tokens
            + self.cache_read_tokens
            + self.reasoning_tokens
        )


@dataclass(frozen=True)
class UsageAggregate:
    """Usage grouped by model, optionally narrowed to one operation type."""

    provider: str
    model: str
    operation_type: str | None
    turn_count: int
    job_count: int
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    reasoning_tokens: int
    cost_usd: float
    iterations: tuple[int, ...]

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_write_tokens
            + self.cache_read_tokens
            + self.reasoning_tokens
        )

    @property
    def iteration_label(self) -> str:
        if not self.iterations:
            return "—"
        if len(self.iterations) == 1:
            return str(self.iterations[0])
        contiguous = self.iterations == tuple(range(self.iterations[0], self.iterations[-1] + 1))
        if contiguous:
            return f"{self.iterations[0]}–{self.iterations[-1]}"
        return ", ".join(str(iteration) for iteration in self.iterations)


@dataclass
class _MutableUsageAggregate:
    turn_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    job_ids: set[str] | None = None
    iterations: set[int] | None = None

    def __post_init__(self) -> None:
        if self.job_ids is None:
            self.job_ids = set()
        if self.iterations is None:
            self.iterations = set()


def _token_value(row: Any, field: str) -> int:
    """Read a token category safely from current or legacy CostRecord-like rows."""
    return int(getattr(row, field, 0) or 0)


def summarize_usage(records: Iterable[Any]) -> UsageTotals:
    """Return overall totals for CostRecord-like objects."""
    rows = list(records)
    job_ids = {str(row.job_id) for row in rows}
    models = {
        (
            _display_value(row.provider, "Unknown provider"),
            _display_value(row.model, "Unknown model"),
        )
        for row in rows
    }
    return UsageTotals(
        turn_count=len(rows),
        job_count=len(job_ids),
        model_count=len(models),
        input_tokens=sum(_token_value(row, "input_tokens") for row in rows),
        output_tokens=sum(_token_value(row, "output_tokens") for row in rows),
        cache_write_tokens=sum(_token_value(row, "cache_write_tokens") for row in rows),
        cache_read_tokens=sum(_token_value(row, "cache_read_tokens") for row in rows),
        reasoning_tokens=sum(_token_value(row, "reasoning_tokens") for row in rows),
        cost_usd=sum(float(row.cost_usd) for row in rows),
    )


def _aggregate_usage(records: Iterable[Any], *, include_operation: bool) -> list[UsageAggregate]:
    grouped: dict[tuple[str, str, str | None], _MutableUsageAggregate] = {}
    for row in records:
        provider = _display_value(row.provider, "Unknown provider")
        model = _display_value(row.model, "Unknown model")
        operation = _display_value(row.operation_type, "Unspecified") if include_operation else None
        key = (provider, model, operation)
        aggregate = grouped.setdefault(key, _MutableUsageAggregate())
        aggregate.turn_count += 1
        aggregate.input_tokens += _token_value(row, "input_tokens")
        aggregate.output_tokens += _token_value(row, "output_tokens")
        aggregate.cache_write_tokens += _token_value(row, "cache_write_tokens")
        aggregate.cache_read_tokens += _token_value(row, "cache_read_tokens")
        aggregate.reasoning_tokens += _token_value(row, "reasoning_tokens")
        aggregate.cost_usd += float(row.cost_usd)
        assert aggregate.job_ids is not None
        aggregate.job_ids.add(str(row.job_id))
        if row.iteration is not None:
            assert aggregate.iterations is not None
            aggregate.iterations.add(int(row.iteration))

    result = [
        UsageAggregate(
            provider=provider,
            model=model,
            operation_type=operation,
            turn_count=value.turn_count,
            job_count=len(value.job_ids or ()),
            input_tokens=value.input_tokens,
            output_tokens=value.output_tokens,
            cache_write_tokens=value.cache_write_tokens,
            cache_read_tokens=value.cache_read_tokens,
            reasoning_tokens=value.reasoning_tokens,
            cost_usd=value.cost_usd,
            iterations=tuple(sorted(value.iterations or ())),
        )
        for (provider, model, operation), value in grouped.items()
    ]
    return sorted(
        result,
        key=lambda item: (
            -item.total_tokens,
            item.provider.casefold(),
            item.model.casefold(),
            (item.operation_type or "").casefold(),
        ),
    )


def aggregate_model_usage(records: Iterable[Any]) -> list[UsageAggregate]:
    """Aggregate completed turns by provider and model."""
    return _aggregate_usage(records, include_operation=False)


def aggregate_operation_usage(records: Iterable[Any]) -> list[UsageAggregate]:
    """Aggregate completed turns by operation, provider, and model."""
    return _aggregate_usage(records, include_operation=True)
