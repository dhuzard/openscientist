"""Tests for Evidence Librarian planning, approval, and skill composition."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from openscientist.evidence_librarian import (
    TRACE_PATH,
    approve_evidence_plan,
    build_evidence_plan,
    filter_skills_for_plan,
    initialise_evidence_trace,
    load_approved_evidence_plan,
    persist_evidence_plan,
    select_plan_skills,
)


def _skill(
    name: str,
    slug: str,
    category: str,
    *,
    description: str = "",
    tags: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        slug=slug,
        category=category,
        description=description,
        tags=tags or [],
    )


@pytest.fixture
def skills() -> list[SimpleNamespace]:
    return [
        _skill("Result interpretation", "result-interpretation", "workflow"),
        _skill(
            "Single-cell analysis",
            "single-cell-analysis",
            "domain",
            description="Quality control and differential expression for transcriptomics.",
            tags=["h5ad", "genomics"],
        ),
        _skill(
            "Protein crystallography",
            "protein-crystallography",
            "domain",
            description="Refinement and structure validation.",
            tags=["pdb", "structure"],
        ),
    ]


def test_plan_recommends_workflow_and_matching_domain_skill(skills):
    plan = build_evidence_plan(
        "Which genes are differentially expressed in these single-cell populations?",
        [Path("cells.h5ad")],
        skills,
    )

    assert plan.status == "draft"
    assert "workflow--result-interpretation" in plan.selected_skill_keys
    assert "domain--single-cell-analysis" in plan.selected_skill_keys
    assert "domain--protein-crystallography" not in plan.selected_skill_keys
    assert any("systematic review" in query for query in plan.literature_queries)


def test_human_selection_keeps_workflow_skill_mandatory(skills):
    plan = build_evidence_plan("Compare protein structures", ["model.pdb"], skills)
    selected = select_plan_skills(plan, ["domain--protein-crystallography"])

    assert selected.selected_skill_keys == (
        "workflow--result-interpretation",
        "domain--protein-crystallography",
    )
    assert selected.status == "draft"


def test_only_approved_plan_can_be_persisted(tmp_path, skills):
    plan = build_evidence_plan("Compare protein structures", ["model.pdb"], skills)

    with pytest.raises(ValueError, match="approved"):
        persist_evidence_plan(tmp_path, plan)

    approved = approve_evidence_plan(plan, "researcher-123")
    persist_evidence_plan(tmp_path, approved)

    loaded = load_approved_evidence_plan(tmp_path)
    assert loaded is not None
    assert loaded.plan_id == approved.plan_id
    assert (tmp_path / "EVIDENCE_PLAN.md").exists()


def test_approved_plan_filters_domain_skills_but_preserves_workflow(tmp_path, skills):
    plan = build_evidence_plan("Compare protein structures", ["model.pdb"], skills)
    plan = select_plan_skills(plan, ["domain--protein-crystallography"])
    persist_evidence_plan(tmp_path, approve_evidence_plan(plan, "researcher-123"))

    filtered = filter_skills_for_plan(tmp_path, skills)

    assert [f"{skill.category}--{skill.slug}" for skill in filtered] == [
        "workflow--result-interpretation",
        "domain--protein-crystallography",
    ]


def test_no_plan_preserves_legacy_all_skills_behaviour(tmp_path, skills):
    assert filter_skills_for_plan(tmp_path, skills) == skills


def test_trace_records_approved_bundle_once(tmp_path, skills):
    plan = build_evidence_plan("Compare protein structures", ["model.pdb"], skills)
    persist_evidence_plan(tmp_path, approve_evidence_plan(plan, "researcher-123"))

    initialise_evidence_trace(tmp_path)
    initialise_evidence_trace(tmp_path)

    trace_lines = (tmp_path / TRACE_PATH).read_text(encoding="utf-8").splitlines()
    assert len(trace_lines) == 1
    assert "evidence_plan_activated" in trace_lines[0]
