"""Tests for skill-grounded Job creation prompt assistance."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from openscientist.job_brief_assistant import (
    build_job_brief_revision_prompt,
    generate_job_brief_suggestion,
    load_job_brief_skill,
    parse_job_brief_suggestion,
)


def test_load_job_brief_skill_uses_trusted_builtin() -> None:
    skill = load_job_brief_skill()

    assert "name: create-job-brief" in skill
    assert "## Reusable template" in skill
    assert "## DVC-oriented example" in skill


def test_revision_prompt_contains_only_bounded_form_context() -> None:
    prompt = build_job_brief_revision_prompt(
        "  What differs?  ",
        "  Cage is the experimental unit.  ",
        ["C:/private/source/data.csv", "metadata.json"],
    )
    payload = json.loads(prompt[prompt.index("{") :])

    assert payload == {
        "research_question": "What differs?",
        "description": "Cage is the experimental unit.",
        "uploaded_file_names": ["data.csv", "metadata.json"],
        "omitted_file_name_count": 0,
    }
    assert "C:/private/source" not in prompt


def test_parse_job_brief_suggestion_accepts_fenced_json() -> None:
    result = parse_job_brief_suggestion(
        """```json
        {
          "research_question": "Does treatment change cage activity?",
          "description": "Objective: Compare cage-level activity.",
          "open_items": ["Confirm the light schedule."],
          "changes_summary": "Named the unit and blocker."
        }
        ```"""
    )

    assert result.research_question == "Does treatment change cage activity?"
    assert result.description == "Objective: Compare cage-level activity."
    assert result.open_items == ("Confirm the light schedule.",)
    assert result.changes_summary == "Named the unit and blocker."


@pytest.mark.parametrize(
    "raw",
    [
        "not JSON",
        '{"research_question": "Question only", "description": ""}',
        '{"research_question": "Question", "description": "Context", "open_items": {}}',
    ],
)
def test_parse_job_brief_suggestion_rejects_invalid_output(raw: str) -> None:
    with pytest.raises(RuntimeError):
        parse_job_brief_suggestion(raw)


@pytest.mark.asyncio
async def test_generate_job_brief_suggestion_grounds_agent_with_skill() -> None:
    response = json.dumps(
        {
            "research_question": "Does treatment alter cage activity?",
            "description": "Objective: Estimate a cage-level treatment contrast.",
            "open_items": [],
            "changes_summary": "Made the estimand explicit.",
        }
    )
    runner = AsyncMock(return_value=response)

    with patch("openscientist.job_brief_assistant._run_job_brief_agent", runner):
        result = await generate_job_brief_suggestion(
            "Does treatment matter?",
            "Cages were recorded.",
            ["activity.csv"],
        )

    assert runner.await_args is not None
    system_prompt, prompt = runner.await_args.args
    assert "## Trusted create-job-brief skill" in system_prompt
    assert "name: create-job-brief" in system_prompt
    assert "activity.csv" in prompt
    assert result.research_question == "Does treatment alter cage activity?"


@pytest.mark.asyncio
async def test_generate_job_brief_suggestion_requires_user_context() -> None:
    with pytest.raises(ValueError, match="Enter a research question or study context"):
        await generate_job_brief_suggestion(" ", "", [])
