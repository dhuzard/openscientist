"""DVC governance instructions delivered to both supported agent backends."""

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from openscientist.agent.skills import claude_skill_markdown, codex_skill_markdown
from openscientist.database.models import Skill

SKILL_PATH = (
    Path(__file__).parents[1]
    / "skills"
    / "domain"
    / "digital-ventilated-cage-analysis"
    / "SKILL.md"
)
GOVERNED_SEQUENCE = (
    "dvc_import_dataset",
    "dvc_assess_pre_analysis",
    "request authenticated approval",
    "dvc_run_analysis",
    "dvc_assess_post_analysis",
    "Assemble the evidence-linked report",
)


def _dvc_skill() -> Skill:
    source = SKILL_PATH.read_text(encoding="utf-8")
    _, frontmatter, body = source.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    return Skill(
        name=metadata["name"],
        slug=metadata["slug"],
        category=metadata["category"],
        description=metadata["description"],
        content=body.lstrip(),
        is_enabled=True,
    )


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(claude_skill_markdown, id="claude"),
        pytest.param(codex_skill_markdown, id="codex"),
    ],
)
def test_backend_skill_prescribes_exact_fail_closed_dvc_sequence(render):
    rendered = render(_dvc_skill())
    normalized = " ".join(rendered.split())
    offsets = [rendered.index(step) for step in GOVERNED_SEQUENCE]

    assert offsets == sorted(offsets)
    assert "Stop on every failed call." in rendered
    assert "Do not skip or reorder checkpoints" in rendered
    assert "reuse an approval for changed inputs" in normalized
    assert "status: completed" in rendered
    assert "humanReadableId" in rendered
    assert "does not accept the cage UUID" in rendered


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(claude_skill_markdown, id="claude"),
        pytest.param(codex_skill_markdown, id="codex"),
    ],
)
def test_backend_skill_requires_data_science_for_ungoverned_statistics(render):
    rendered = render(_dvc_skill())
    normalized = " ".join(rendered.split())

    assert "Before using `execute_code` for statistical tests" in rendered
    assert "load and follow `data-science` too" in normalized
    assert "If `data-science` is unavailable, block the step" in normalized
    assert "Assignment alone is not use" in rendered


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(claude_skill_markdown, id="claude"),
        pytest.param(codex_skill_markdown, id="codex"),
    ],
)
def test_backend_skill_blocks_assumed_circadian_phase_and_labels_fallbacks(render):
    rendered = render(_dvc_skill())
    normalized = " ".join(rendered.split())

    assert "Missing, inferred, placeholder, or contradictory schedules are hard stops" in normalized
    assert "Never assume 18:00 UTC" in rendered
    assert "do not substitute `execute_code` for `dvc_run_analysis`" in normalized
    assert "Never call these outputs approved, governed, confirmatory" in normalized
    assert "governance scope: governed, validation diagnostic" in normalized


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(claude_skill_markdown, id="claude"),
        pytest.param(codex_skill_markdown, id="codex"),
    ],
)
def test_backend_skill_prescribes_professional_cage_first_analysis_contract(render):
    rendered = render(_dvc_skill())
    normalized = " ".join(rendered.split())

    assert "Freeze a cage reconciliation table" in rendered
    assert "declared common time grid" in normalized
    assert "Never smooth across a gap" in rendered
    assert "from local lights-on to the next lights-on" in normalized
    assert "half-open `[start, end)` intervals" in normalized
    assert "leave-one-site-out reference" in normalized
    assert "failed gate is a failed analysis" in normalized


def test_skill_stays_discoverable_and_context_efficient() -> None:
    source = SKILL_PATH.read_text(encoding="utf-8")
    skill = _dvc_skill()

    assert len(source.splitlines()) < 500
    assert "audit, repair, or interpret" in (skill.description or "")
    assert "review of an existing DVC pipeline" in (skill.description or "")
