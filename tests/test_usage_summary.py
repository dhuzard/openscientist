"""Tests for shared model/task token usage aggregation."""

from types import SimpleNamespace
from uuid import uuid4

from openscientist.usage_summary import (
    aggregate_model_usage,
    aggregate_operation_usage,
    summarize_usage,
)


def _record(
    *,
    job_id: object,
    provider: str = "OpenAI API",
    model: str = "gpt-5.5",
    operation: str = "discovery",
    iteration: int | None = 1,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    reasoning_tokens: int = 0,
    cost_usd: float = 0.001,
) -> SimpleNamespace:
    return SimpleNamespace(
        job_id=job_id,
        provider=provider,
        model=model,
        operation_type=operation,
        iteration=iteration,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_read_tokens=cache_read_tokens,
        reasoning_tokens=reasoning_tokens,
        cost_usd=cost_usd,
    )


def test_summarize_usage_counts_distinct_jobs_models_and_tokens() -> None:
    first_job = uuid4()
    second_job = uuid4()
    records = [
        _record(
            job_id=first_job,
            input_tokens=100,
            output_tokens=25,
            cache_read_tokens=40,
            reasoning_tokens=5,
            cost_usd=0.01,
        ),
        _record(
            job_id=first_job,
            operation="report",
            iteration=None,
            input_tokens=300,
            output_tokens=75,
            cache_write_tokens=20,
            cost_usd=0.03,
        ),
        _record(
            job_id=second_job,
            provider="Anthropic",
            model="claude-sonnet-4-6",
            input_tokens=50,
            output_tokens=10,
            cost_usd=0.02,
        ),
    ]

    totals = summarize_usage(records)

    assert totals.turn_count == 3
    assert totals.job_count == 2
    assert totals.model_count == 2
    assert totals.input_tokens == 450
    assert totals.output_tokens == 110
    assert totals.cache_read_tokens == 40
    assert totals.cache_write_tokens == 20
    assert totals.reasoning_tokens == 5
    assert totals.total_tokens == 625
    assert totals.cost_usd == 0.06


def test_model_usage_groups_turns_and_formats_non_contiguous_iterations() -> None:
    job_id = uuid4()
    records = [
        _record(job_id=job_id, iteration=1, input_tokens=100, cache_read_tokens=30),
        _record(job_id=job_id, iteration=3, input_tokens=200, reasoning_tokens=10),
        _record(
            job_id=uuid4(),
            provider="Anthropic",
            model="claude-sonnet-4-6",
            input_tokens=50,
        ),
    ]

    rows = aggregate_model_usage(records)

    assert rows[0].model == "gpt-5.5"
    assert rows[0].turn_count == 2
    assert rows[0].job_count == 1
    assert rows[0].input_tokens == 300
    assert rows[0].cache_read_tokens == 30
    assert rows[0].reasoning_tokens == 10
    assert rows[0].total_tokens == 380
    assert rows[0].iterations == (1, 3)
    assert rows[0].iteration_label == "1, 3"


def test_operation_usage_keeps_model_and_task_boundaries_explicit() -> None:
    job_id = uuid4()
    records = [
        _record(job_id=job_id, operation="discovery", iteration=1),
        _record(job_id=job_id, operation="discovery", iteration=2),
        _record(job_id=job_id, operation="report", iteration=None, input_tokens=500),
    ]

    rows = aggregate_operation_usage(records)

    assert [(row.operation_type, row.model) for row in rows] == [
        ("report", "gpt-5.5"),
        ("discovery", "gpt-5.5"),
    ]
    discovery = rows[1]
    assert discovery.turn_count == 2
    assert discovery.iteration_label == "1–2"


def test_blank_metadata_is_reported_as_unknown_instead_of_hidden() -> None:
    row = aggregate_operation_usage([_record(job_id=uuid4(), provider="", model="", operation="")])[
        0
    ]

    assert row.provider == "Unknown provider"
    assert row.model == "Unknown model"
    assert row.operation_type == "Unspecified"
