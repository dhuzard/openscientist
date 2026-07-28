"""Behavioral guards for the human-in-the-loop Skill Creator page."""

from openscientist.skill_authoring import SkillValidationFinding
from openscientist.webapp_components.pages.skill_create import _brief_ready, _can_export


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
