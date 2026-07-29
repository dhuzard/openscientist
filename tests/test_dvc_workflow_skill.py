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
