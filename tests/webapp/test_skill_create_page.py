"""Behavioral guards for the human-in-the-loop Skill Creator page."""

from pathlib import Path

from openscientist.skill_authoring import SkillValidationFinding
from openscientist.webapp_components.pages.skill_create import (
    _FIELD_HELP,
    _GOOD_SKILL_PRINCIPLES,
    _brief_ready,
    _can_export,
    _format_field_help,
)


def test_general_skill_theory_covers_core_authoring_principles() -> None:
    titles = {title for _icon, title, _description in _GOOD_SKILL_PRINCIPLES}
    assert titles == {
        "Discoverable",
        "Non-obvious and reusable",
        "Right degree of freedom",
        "Context-efficient",
        "Contract-driven",
        "Testable and revisable",
    }
    assert all(icon for icon, _title, _description in _GOOD_SKILL_PRINCIPLES)
    assert all(
        len(description.split()) >= 10 for _icon, _title, description in _GOOD_SKILL_PRINCIPLES
    )


def test_every_skill_authoring_text_field_has_theory_patterns_and_a_traced_example() -> None:
    expected_fields = {
        "proposed_name",
        "purpose",
        "triggers",
        "inputs",
        "workflow",
        "outputs",
        "guardrails",
        "examples",
        "draft",
        "feedback",
    }
    assert set(_FIELD_HELP) == expected_fields

    repository_root = Path(__file__).parents[2]
    for field_key, help_content in _FIELD_HELP.items():
        assert len(help_content.theory.split()) >= 12
        assert len(help_content.approaches) >= 2
        assert all(len(approach.split()) >= 4 for approach in help_content.approaches)
        assert help_content.skill_name
        assert len(help_content.example.split()) >= 12
        assert (repository_root / help_content.source_path).is_file()

        rendered = _format_field_help(field_key)
        assert "HOW TO WRITE IT" in rendered
        assert "VALID PATTERNS" in rendered
        assert "REAL EXAMPLE" in rendered
        assert "SOURCE" in rendered


def test_brief_requires_human_purpose_and_triggers() -> None:
    assert not _brief_ready({"purpose": "Analyze assays", "triggers": ""})
    assert not _brief_ready({"purpose": "", "triggers": "Use for assay tables"})
    assert _brief_ready(
        {
            "purpose": "Analyze assays",
            "triggers": "Use for assay tables; not for images",
        }
    )


def test_export_requires_acceptance_and_no_errors() -> None:
    brief = {"purpose": "Analyze assays", "triggers": "Use for assay tables"}
    warning = (SkillValidationFinding("warning", "review", "Review this."),)
    error = (SkillValidationFinding("error", "invalid", "Fix this."),)

    assert not _can_export(brief, warning, accepted=False)
    assert not _can_export(brief, error, accepted=True)
    assert _can_export(brief, warning, accepted=True)
