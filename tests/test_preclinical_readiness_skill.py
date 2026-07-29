"""FAIR/PREPARE/ARRIVE readiness instructions for both agent backends."""

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from openscientist.agent.skills import claude_skill_markdown, codex_skill_markdown
from openscientist.database.models import Skill
from openscientist.skill_ingestion import SkillParser

SKILL_DIR = Path(__file__).parents[1] / "skills" / "domain" / "preclinical-study-readiness"
SKILL_PATH = SKILL_DIR / "SKILL.md"
GOVERNED_SEQUENCE = (
    "dvc_import_dataset",
    "dvc_assess_pre_analysis",
    "Present the versioned FAIR, PREPARE, and ARRIVE findings",
    "request authenticated approval",
    "dvc_run_analysis",
    "dvc_assess_post_analysis",
)


def _readiness_skill() -> Skill:
    parsed = SkillParser().parse_file(SKILL_PATH)
    return Skill(
        name=parsed.name,
        slug=parsed.slug,
        category=parsed.category,
        description=parsed.description,
        content=parsed.content,
        is_enabled=True,
    )


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(claude_skill_markdown, id="claude"),
        pytest.param(codex_skill_markdown, id="codex"),
    ],
)
def test_backend_skill_preserves_assessment_and_approval_boundaries(render) -> None:
    rendered = render(_readiness_skill())
    offsets = [rendered.index(step) for step in GOVERNED_SEQUENCE]

    assert offsets == sorted(offsets)
    assert "authoritative rules engine" in rendered
    assert "does not certify compliance" in rendered
    assert "The agent must not create, alter, or impersonate this approval." in rendered
    assert "never reuse the old checkpoint" in rendered
    assert "do not simulate FAIR-VCG output" in rendered


def test_skill_distinguishes_framework_roles_and_epistemic_states() -> None:
    content = SKILL_PATH.read_text(encoding="utf-8")

    for framework in ("**FAIR**", "**PREPARE**", "**ARRIVE**"):
        assert framework in content
    for state in ("recorded", "computed", "inferred", "unknown"):
        assert f"`{state}`" in content
    for status in (
        "`satisfied`",
        "`partial`",
        "`missing`",
        "`not_applicable`",
        "`conflicting`",
    ):
        assert status in content


def test_skill_is_self_contained_for_openscientist_transport() -> None:
    content = SKILL_PATH.read_text(encoding="utf-8")
    metadata = yaml.safe_load((SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8"))

    assert "references/" not in content
    assert metadata["interface"]["default_prompt"].startswith("Use $preclinical-study-readiness")
