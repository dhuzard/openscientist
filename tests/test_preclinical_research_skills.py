"""Structural and safety invariants for the preclinical research skill suite."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from openscientist.skill_authoring import validate_skill_markdown
from openscientist.skill_ingestion import SkillParser

SKILLS_ROOT = Path(__file__).parents[1] / "skills" / "domain"
SKILL_SLUGS = (
    "preclinical-preregistration",
    "preclinical-power-statistics",
    "preclinical-experimental-design",
    "fair-data-stewardship",
    "arrive-2-reporting",
    "prepare-study-planning",
)


@pytest.mark.parametrize("slug", SKILL_SLUGS)
def test_skill_is_ingestible_self_contained_and_export_ready(slug: str) -> None:
    path = SKILLS_ROOT / slug / "SKILL.md"
    markdown = path.read_text(encoding="utf-8")
    parsed = SkillParser().parse_file(path)
    errors = [
        finding for finding in validate_skill_markdown(markdown) if finding.severity == "error"
    ]

    assert errors == []
    assert parsed.category == "domain"
    assert parsed.slug == slug
    assert "references/" not in markdown
    assert "## Export-ready output" in markdown
    assert "## Stop conditions" in markdown
    assert '"source_register"' in markdown
    assert '"contradictions"' in markdown
    assert '"human_confirmations_required"' in markdown

    json_blocks = re.findall(r"```json\n(.*?)\n```", markdown, re.DOTALL)
    assert len(json_blocks) == 1
    envelope = json.loads(json_blocks[0])
    assert envelope["schema_version"].startswith("openscientist-")


def test_preregistration_api_contract_is_read_only_and_secret_safe() -> None:
    markdown = (SKILLS_ROOT / "preclinical-preregistration" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "GET https://preclinicaltrials.eu/api/external/viewable-protocols" in markdown
    assert "read-only external endpoint" in markdown
    assert "Do not infer create, update, submit, or delete API capabilities" in markdown
    assert "Never place the token" in markdown
    assert re.search(r"(?i)authorization:\s*bearer\s+[A-Za-z0-9]{20,}", markdown) is None


def test_full_fair_arrive_and_prepare_topologies_are_present() -> None:
    fair = (SKILLS_ROOT / "fair-data-stewardship" / "SKILL.md").read_text(encoding="utf-8")
    arrive = (SKILLS_ROOT / "arrive-2-reporting" / "SKILL.md").read_text(encoding="utf-8")
    prepare = (SKILLS_ROOT / "prepare-study-planning" / "SKILL.md").read_text(encoding="utf-8")

    for principle in (
        "F1",
        "F2",
        "F3",
        "F4",
        "A1",
        "A1.1",
        "A1.2",
        "A2",
        "I1",
        "I2",
        "I3",
        "R1",
        "R1.1",
        "R1.2",
        "R1.3",
    ):
        assert f"`{principle}`" in fair

    for item in range(1, 22):
        assert re.search(rf"^{item}\. ", arrive, re.MULTILINE)
    for topic in range(1, 16):
        assert re.search(rf"^{topic}\. ", prepare, re.MULTILINE)


def test_design_and_statistics_fail_closed_on_high_risk_assumptions() -> None:
    design = (SKILLS_ROOT / "preclinical-experimental-design" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    statistics = (SKILLS_ROOT / "preclinical-power-statistics" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "Never invent or recall a dose as authoritative" in design
    assert "veterinarian-approved institutional SOP" in design
    assert "Never silently default to 80% power" in statistics
    assert "verify with a second implementation" in statistics
