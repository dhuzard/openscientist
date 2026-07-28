"""Evidence planning and per-job skill composition.

The Evidence Librarian is deliberately a preflight planner, not an installer.
It ranks enabled skills against a research question and the submitted file
types, proposes bibliographic searches, and produces a reviewable plan.  Only
an explicitly approved plan may narrow the skills materialised for a job.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from openscientist.database.models import Skill
from openscientist.database.session import AsyncSessionLocal
from openscientist.prompts import get_enabled_skills

PLAN_DIRECTORY = ".openscientist"
PLAN_JSON = "evidence_plan.json"
PLAN_MARKDOWN = "EVIDENCE_PLAN.md"
TRACE_PATH = Path("provenance") / "evidence_librarian" / "trace.jsonl"
SCHEMA_VERSION = 1

_TOKEN = re.compile(r"[a-z][a-z0-9+-]{2,}")
_STOP_WORDS = {
    "about",
    "after",
    "against",
    "among",
    "analysis",
    "data",
    "does",
    "from",
    "have",
    "into",
    "most",
    "scientific",
    "that",
    "their",
    "these",
    "this",
    "using",
    "what",
    "when",
    "where",
    "which",
    "with",
}
_FILE_HINTS = {
    ".cif": ("protein", "structure", "crystallography"),
    ".fasta": ("sequence", "genomics", "protein"),
    ".fastq": ("sequencing", "genomics", "transcriptomics"),
    ".h5ad": ("single-cell", "transcriptomics", "genomics"),
    ".pdb": ("protein", "structure", "crystallography"),
    ".sdf": ("chemistry", "molecule", "structure"),
    ".vcf": ("variants", "genomics", "genetics"),
}


@dataclass(frozen=True)
class SkillCandidate:
    """One skill considered for a job-specific bundle."""

    key: str
    name: str
    category: str
    description: str
    score: float
    reasons: tuple[str, ...]
    recommended: bool


@dataclass(frozen=True)
class EvidencePlan:
    """A reviewable and serialisable evidence-and-skill plan."""

    schema_version: int
    plan_id: str
    status: str
    research_question: str
    data_files: tuple[str, ...]
    created_at: str
    approved_at: str | None
    approver_id: str | None
    candidates: tuple[SkillCandidate, ...]
    selected_skill_keys: tuple[str, ...]
    literature_queries: tuple[str, ...]
    inclusion_criteria: tuple[str, ...]
    exclusion_criteria: tuple[str, ...]
    execution_steps: tuple[str, ...]
    conflict_policy: tuple[str, ...]
    trace_contract: tuple[str, ...]


def _normalise_tokens(value: str) -> set[str]:
    return {token for token in _TOKEN.findall(value.lower()) if token not in _STOP_WORDS}


def _skill_key(skill: Skill) -> str:
    return f"{skill.category}--{skill.slug}"


def _tag_text(tags: Any) -> str:
    if isinstance(tags, list):
        return " ".join(str(tag) for tag in tags)
    if isinstance(tags, dict):
        return " ".join(f"{key} {value}" for key, value in tags.items())
    return str(tags or "")


def _file_terms(data_files: Iterable[str | Path]) -> set[str]:
    terms: set[str] = set()
    for data_file in data_files:
        path = Path(data_file)
        terms.update(_normalise_tokens(path.stem))
        terms.update(_FILE_HINTS.get(path.suffix.lower(), ()))
    return terms


def _rank_skill(skill: Skill, task_terms: set[str], file_terms: set[str]) -> SkillCandidate:
    key = _skill_key(skill)
    if skill.category == "workflow":
        return SkillCandidate(
            key=key,
            name=skill.name,
            category=skill.category,
            description=skill.description or "",
            score=100.0,
            reasons=("Required cross-domain workflow",),
            recommended=True,
        )

    name_terms = _normalise_tokens(f"{skill.name} {skill.slug} {skill.category}")
    description_terms = _normalise_tokens(skill.description or "")
    tag_terms = _normalise_tokens(_tag_text(skill.tags))
    name_hits = sorted(task_terms & name_terms)
    description_hits = sorted(task_terms & description_terms)
    tag_hits = sorted(task_terms & tag_terms)
    file_hits = sorted(file_terms & (name_terms | description_terms | tag_terms))
    score = (
        len(name_hits) * 4.0
        + len(tag_hits) * 3.0
        + len(description_hits) * 2.0
        + len(file_hits) * 3.0
    )
    reasons: list[str] = []
    if name_hits:
        reasons.append(f"Task terms match name/category: {', '.join(name_hits[:5])}")
    if tag_hits:
        reasons.append(f"Task terms match tags: {', '.join(tag_hits[:5])}")
    if description_hits:
        reasons.append(f"Task terms match description: {', '.join(description_hits[:5])}")
    if file_hits:
        reasons.append(f"Submitted file type suggests: {', '.join(file_hits[:5])}")
    if not reasons:
        reasons.append("No direct task or file-type match")

    return SkillCandidate(
        key=key,
        name=skill.name,
        category=skill.category,
        description=skill.description or "",
        score=score,
        reasons=tuple(reasons),
        recommended=score > 0,
    )


def _literature_queries(question: str, domain_terms: set[str]) -> tuple[str, ...]:
    clean_question = " ".join(question.split()).strip()
    queries = [clean_question]
    if domain_terms:
        terms = " ".join(sorted(domain_terms)[:4])
        queries.append(f"({clean_question}) AND ({terms})")
    queries.extend(
        (
            f"({clean_question}) AND (systematic review OR meta-analysis)",
            f"({clean_question}) AND (mechanism OR pathway)",
        )
    )
    return tuple(dict.fromkeys(query[:500] for query in queries if query))


def build_evidence_plan(
    research_question: str,
    data_files: Iterable[str | Path],
    skills: Iterable[Skill],
    *,
    max_domain_skills: int = 5,
) -> EvidencePlan:
    """Build a deterministic draft plan for human review."""

    question = " ".join(research_question.split()).strip()
    if not question:
        raise ValueError("A research question is required to prepare an evidence plan.")

    file_names = tuple(Path(path).name for path in data_files)
    task_terms = _normalise_tokens(question)
    file_terms = _file_terms(file_names)
    candidates = [_rank_skill(skill, task_terms, file_terms) for skill in skills]
    candidates.sort(
        key=lambda candidate: (
            candidate.category != "workflow",
            -candidate.score,
            candidate.name.lower(),
        )
    )

    workflow_keys = [candidate.key for candidate in candidates if candidate.category == "workflow"]
    domain_keys = [
        candidate.key
        for candidate in candidates
        if candidate.category != "workflow" and candidate.recommended
    ][:max_domain_skills]
    selected = tuple(workflow_keys + domain_keys)
    matched_terms = task_terms | file_terms

    return EvidencePlan(
        schema_version=SCHEMA_VERSION,
        plan_id=str(uuid4()),
        status="draft",
        research_question=question,
        data_files=file_names,
        created_at=datetime.now(UTC).isoformat(),
        approved_at=None,
        approver_id=None,
        candidates=tuple(candidates),
        selected_skill_keys=selected,
        literature_queries=_literature_queries(question, matched_terms),
        inclusion_criteria=(
            "Directly addresses the population, system, intervention, exposure, or mechanism.",
            "Reports methods and evidence that can be assessed for relevance and quality.",
            "Prefer primary studies for claims; use reviews to map terminology and prior consensus.",
        ),
        exclusion_criteria=(
            "No inspectable abstract, methods, or evidence relevant to the research question.",
            "Duplicate publication or secondary summary when the primary source is available.",
            "Claims whose study system cannot reasonably inform the submitted data.",
        ),
        execution_steps=(
            "Inspect the submitted data and verify that the approved skill bundle still applies.",
            "Run the planned broad literature search and record inclusion/exclusion decisions.",
            "Apply workflow skills in their required phase order.",
            "Apply selected domain skills only when their prerequisites are satisfied.",
            "Triangulate data-derived findings with literature; record disagreement and uncertainty.",
            "Audit claims, citations, skill use, and deviations before producing the final report.",
        ),
        conflict_policy=(
            "Researcher-approved plan overrides automatic domain-skill recommendations.",
            "Safety and scientific-validity guardrails override convenience or speed.",
            "When skills conflict, prefer the more specific applicable skill and record the conflict.",
            "Do not silently add, publish, or install a skill; record proposed deviations for review.",
        ),
        trace_contract=(
            "Record every selected skill and its version/hash in the job bundle manifest.",
            "Record each literature query and the source identifiers used for consequential claims.",
            "Separate data observations, associations, hypotheses, and causal conclusions.",
            "Record deviations from this plan with the reason and resulting uncertainty.",
        ),
    )


async def build_evidence_plan_from_enabled_skills(
    research_question: str,
    data_files: Iterable[str | Path],
) -> EvidencePlan:
    """Load enabled skills and prepare a draft plan."""

    async with AsyncSessionLocal(thread_safe=True) as session:
        skills = await get_enabled_skills(session)
    return build_evidence_plan(research_question, data_files, skills)


def select_plan_skills(plan: EvidencePlan, selected_keys: Iterable[str]) -> EvidencePlan:
    """Apply a human's bundle selection and invalidate any prior approval."""

    valid = {candidate.key for candidate in plan.candidates}
    required = {candidate.key for candidate in plan.candidates if candidate.category == "workflow"}
    requested = {key for key in selected_keys if key in valid}
    ordered = tuple(
        candidate.key for candidate in plan.candidates if candidate.key in requested | required
    )
    return replace(
        plan,
        status="draft",
        approved_at=None,
        approver_id=None,
        selected_skill_keys=ordered,
    )


def approve_evidence_plan(plan: EvidencePlan, approver_id: str) -> EvidencePlan:
    """Return an immutable approved plan."""

    if not approver_id.strip():
        raise ValueError("An authenticated approver is required.")
    return replace(
        plan,
        status="approved",
        approved_at=datetime.now(UTC).isoformat(),
        approver_id=approver_id,
    )


def evidence_plan_to_dict(plan: EvidencePlan) -> dict[str, Any]:
    """Convert a plan into its JSON-safe representation."""

    return asdict(plan)


def evidence_plan_from_dict(payload: dict[str, Any]) -> EvidencePlan:
    """Validate the persisted shape and reconstruct a plan."""

    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported evidence plan schema version.")
    candidates = tuple(
        SkillCandidate(**{**candidate, "reasons": tuple(candidate["reasons"])})
        for candidate in payload["candidates"]
    )
    tuple_fields = (
        "data_files",
        "selected_skill_keys",
        "literature_queries",
        "inclusion_criteria",
        "exclusion_criteria",
        "execution_steps",
        "conflict_policy",
        "trace_contract",
    )
    values = dict(payload)
    values["candidates"] = candidates
    for field in tuple_fields:
        values[field] = tuple(values[field])
    return EvidencePlan(**values)


def render_evidence_plan_markdown(plan: EvidencePlan) -> str:
    """Render the human-readable agent contract stored with the job."""

    selected = {
        candidate.key: candidate
        for candidate in plan.candidates
        if candidate.key in plan.selected_skill_keys
    }

    def bullets(values: Iterable[str]) -> str:
        return "\n".join(f"- {value}" for value in values)

    skill_lines = [
        f"- `{key}` — {selected[key].name}: {'; '.join(selected[key].reasons)}"
        for key in plan.selected_skill_keys
        if key in selected
    ]
    if not skill_lines:
        skill_lines = ["- No skills selected."]
    return f"""# Human-approved Evidence Plan

Plan ID: `{plan.plan_id}`
Status: **{plan.status}**
Approved by: `{plan.approver_id or "not approved"}`
Approved at: `{plan.approved_at or "not approved"}`

## Research question

{plan.research_question}

## Approved skill bundle

{chr(10).join(skill_lines)}

## Planned literature queries

{bullets(f"`{query}`" for query in plan.literature_queries)}

## Inclusion criteria

{bullets(plan.inclusion_criteria)}

## Exclusion criteria

{bullets(plan.exclusion_criteria)}

## Execution order

{chr(10).join(f"{index}. {step}" for index, step in enumerate(plan.execution_steps, 1))}

## Conflict policy

{bullets(plan.conflict_policy)}

## Trace contract

{bullets(plan.trace_contract)}
"""


def persist_evidence_plan(job_dir: Path, plan: EvidencePlan | dict[str, Any]) -> None:
    """Persist an approved plan as JSON plus an agent-readable contract."""

    parsed = evidence_plan_from_dict(plan) if isinstance(plan, dict) else plan
    if parsed.status != "approved" or not parsed.approved_at or not parsed.approver_id:
        raise ValueError("Only an explicitly approved evidence plan may be persisted.")
    plan_dir = job_dir / PLAN_DIRECTORY
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / PLAN_JSON).write_text(
        json.dumps(evidence_plan_to_dict(parsed), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (job_dir / PLAN_MARKDOWN).write_text(
        render_evidence_plan_markdown(parsed),
        encoding="utf-8",
    )


def load_approved_evidence_plan(job_dir: Path) -> EvidencePlan | None:
    """Load an approved plan, returning ``None`` for absent or draft plans."""

    path = job_dir / PLAN_DIRECTORY / PLAN_JSON
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    plan = evidence_plan_from_dict(payload)
    if plan.status != "approved" or not plan.approved_at or not plan.approver_id:
        return None
    return plan


def filter_skills_for_plan(job_dir: Path, skills: Iterable[Skill]) -> list[Skill]:
    """Return only the approved per-job bundle, preserving legacy behaviour otherwise."""

    skill_list = list(skills)
    try:
        plan = load_approved_evidence_plan(job_dir)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return skill_list
    if plan is None:
        return skill_list
    selected = set(plan.selected_skill_keys)
    return [
        skill
        for skill in skill_list
        if _skill_key(skill) in selected or skill.category == "workflow"
    ]


def initialise_evidence_trace(job_dir: Path) -> None:
    """Create the first provenance record for an approved plan once."""

    try:
        plan = load_approved_evidence_plan(job_dir)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return
    if plan is None:
        return
    trace_path = job_dir / TRACE_PATH
    if trace_path.exists():
        return
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": "evidence_plan_activated",
        "plan_id": plan.plan_id,
        "approver_id": plan.approver_id,
        "selected_skill_keys": list(plan.selected_skill_keys),
        "literature_queries": list(plan.literature_queries),
    }
    trace_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
