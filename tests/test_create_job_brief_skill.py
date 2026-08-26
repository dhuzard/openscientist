"""Contract tests for the job-brief creation workflow skill."""

from pathlib import Path

import pytest

from openscientist.agent.skills import claude_skill_markdown, codex_skill_markdown
from openscientist.database.models import Skill
from openscientist.skill_ingestion import SkillParser

SKILL_PATH = Path(__file__).parents[1] / "skills" / "workflow" / "create-job-brief" / "SKILL.md"


def _job_brief_skill() -> Skill:
    parsed = SkillParser().parse_file(SKILL_PATH)
    return Skill(
        name=parsed.name,
        slug=parsed.slug,
        category=parsed.category,
        description=parsed.description,
        content=parsed.content,
        is_enabled=True,
    )


def test_job_brief_skill_is_discoverable_as_workflow() -> None:
    parsed = SkillParser().parse_file(SKILL_PATH)

    assert parsed.name == "create-job-brief"
    assert parsed.slug == "create-job-brief"
    assert parsed.category == "workflow"
    assert parsed.description is not None
    assert "creating a job" in parsed.description
    assert "experimental units or estimands" in parsed.description


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(claude_skill_markdown, id="claude"),
        pytest.param(codex_skill_markdown, id="codex"),
    ],
)
def test_job_brief_contract_reaches_both_agent_backends(render) -> None:
    rendered = render(_job_brief_skill())
    normalized = " ".join(rendered.split())

    assert "Research Question" in rendered
    assert "Study Context / Description" in rendered
    assert "independent experimental unit" in normalized
    assert "Map every input file to its role" in normalized
    assert "Define the primary estimand or outcome" in normalized
    assert "exact blockers and the smallest missing information" in normalized
    assert "Do not silently exclude or impute observations" in normalized


def test_job_brief_skill_contains_template_and_dvc_example() -> None:
    source = SKILL_PATH.read_text(encoding="utf-8")

    assert "## Reusable template" in source
    assert "## DVC-oriented example" in source
    assert "The physical cage is the experimental unit" in source
    assert "Type1_*.csv" in source
    assert "evidence-linked reproducibility summary" in source
    assert len(source.splitlines()) < 250
