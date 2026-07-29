"""Interactive, human-in-the-loop Skill Creator page."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from nicegui import ui

from openscientist.auth import require_auth
from openscientist.skill_authoring import (
    SkillAuthoringBrief,
    SkillDraftRequest,
    SkillValidationFinding,
    generate_skill_draft,
    starter_skill_markdown,
    validate_skill_markdown,
)
from openscientist.webapp_components.ui_components import render_navigator
from openscientist.webapp_components.utils import ClientGuard, get_event_value

logger = logging.getLogger(__name__)

_SEVERITY_ICON = {"error": "error", "warning": "warning", "info": "info"}
_SEVERITY_COLOR = {"error": "negative", "warning": "warning", "info": "info"}


@dataclass(frozen=True)
class _FieldWritingHelp:
    """Field-specific theory, alternative patterns, and a traced real example."""

    theory: str
    approaches: tuple[str, ...]
    skill_name: str
    source_path: str
    example: str


_DVC_SKILL = "Digital Ventilated Cage Analysis"
_DVC_SKILL_PATH = "skills/domain/digital-ventilated-cage-analysis/SKILL.md"
_GOOD_SKILL_PRINCIPLES = (
    (
        "travel_explore",
        "Discoverable",
        "The description says what the skill does and gives concrete positive triggers and "
        "nearby non-triggers.",
    ),
    (
        "tips_and_updates",
        "Non-obvious and reusable",
        "It teaches domain knowledge, decisions, or procedures that a capable general agent "
        "should not have to rediscover.",
    ),
    (
        "tune",
        "Right degree of freedom",
        "It uses heuristics when several approaches are valid, and exact steps or scripts when "
        "the task is fragile.",
    ),
    (
        "compress",
        "Context-efficient",
        "It stays concise and operational. In the current OpenScientist runtime, all required "
        "behavior must remain self-contained in SKILL.md.",
    ),
    (
        "fact_check",
        "Contract-driven",
        "It defines consequential inputs, workflow decisions, evidence-rich outputs, stopping "
        "conditions, and safe missing-input behavior.",
    ),
    (
        "science",
        "Testable and revisable",
        "Normal, near-miss, and edge cases make behavior observable; real use and review traces "
        "then drive focused revisions.",
    ),
)


def _dvc_help(
    theory: str,
    approaches: tuple[str, ...],
    example: str,
) -> _FieldWritingHelp:
    """Build field guidance traced to the bundled DVC skill."""

    return _FieldWritingHelp(
        theory=theory,
        approaches=approaches,
        skill_name=_DVC_SKILL,
        source_path=_DVC_SKILL_PATH,
        example=example,
    )


_FIELD_HELP = {
    "proposed_name": _dvc_help(
        theory=(
            "Choose a short, distinctive capability name that helps people recognize the "
            "skill and can become a stable lowercase, hyphenated slug."
        ),
        approaches=(
            "Name the domain and action, such as “Digital Ventilated Cage Analysis”.",
            "Name the outcome, such as “Evidence-backed result interpretation”.",
            "Namespace by a tool or platform when that prevents ambiguity.",
        ),
        example=(
            '"Digital Ventilated Cage Analysis" identifies both the scientific data domain '
            "and the analysis responsibility. It is specific and recognizable without "
            "embedding an incidental implementation detail."
        ),
    ),
    "purpose": _dvc_help(
        theory=(
            "State the repeatable capability the skill adds and the quality improvement it "
            "should produce. Include knowledge or judgment that a general agent should not "
            "have to rediscover."
        ),
        approaches=(
            "Role-led: define the specialist stance the agent should adopt.",
            "Outcome-led: define the decision, artifact, or result that should improve.",
            "Failure-led: define the recurring error or ambiguity the skill prevents.",
        ),
        example=(
            "Act as a metadata-aware DVC analysis specialist: separate data-contract facts, "
            "deterministic computations, literature-supported interpretations, hypotheses, "
            "and unresolved unknowns, preferring a narrower defensible conclusion over a "
            "broad unsupported one."
        ),
    ),
    "triggers": _dvc_help(
        theory=(
            "Define the observable situations that should activate the skill and nearby cases "
            "that should not. Trigger guidance belongs in the description because metadata is "
            "used before the full body is loaded."
        ),
        approaches=(
            "Intent-led: list representative user requests or decisions.",
            "Artifact-led: list file types, schemas, instruments, or APIs.",
            "Boundary-led: pair positive triggers with explicit non-triggers.",
        ),
        example=(
            "Use for DVC Analytics Type 1, Type 2, Type 3, event, ALI, bedding, rest-wake, "
            "RDI, running-wheel, or cage-monitoring questions. Do not use Type 1 or Type 2 "
            "group-housed activity to infer individual animal trajectories."
        ),
    ),
    "inputs": _dvc_help(
        theory=(
            "Describe prerequisites by consequence, not as a generic completeness checklist. "
            "Say which inputs are required, which are optional, and what the agent must do when "
            "a consequential input is missing."
        ),
        approaches=(
            "Checklist: enumerate required data, metadata, tools, and approvals.",
            "Required/optional table: pair each input with its role and fallback.",
            "Staged discovery: inspect first, then ask only questions that change the plan.",
        ),
        example=(
            "Before biological analysis, establish the objective and experimental unit, "
            "cage-to-group assignments, time-valid occupancy, metric and software version, "
            "timestamp semantics, timezone and light schedule, event dictionary, relevant "
            "husbandry, source files, hashes, and previous transformations."
        ),
    ),
    "workflow": _dvc_help(
        theory=(
            "Match specificity to fragility: use flexible heuristics when several approaches "
            "are valid, but prescribe ordered checks or deterministic scripts when mistakes "
            "would be costly or hard to detect."
        ),
        approaches=(
            "Ordered procedure for fragile, repeatable operations.",
            "Decision tree for workflows whose next step depends on evidence.",
            "Phase-based guidance with checkpoints for open-ended investigation.",
        ),
        example=(
            "Identify each export from its structure; preserve source time while deriving "
            "UTC; normalize without interpreting labels; run coverage, missingness, cadence, "
            "variance, and event-overlap checks; reconcile matching Type 1 and Type 2 data; "
            "propose a guarded plan; then replan after quality control."
        ),
    ),
    "outputs": _dvc_help(
        theory=(
            "Define an observable completion contract so another person can tell whether the "
            "skill succeeded. Specify required content, provenance, uncertainty, and the form "
            "of the deliverable without overconstraining harmless presentation choices."
        ),
        approaches=(
            "Artifact contract: name required files, sections, or structured fields.",
            "Evidence checklist: define what every result or claim must carry.",
            "Acceptance example: show a concise representative finished result.",
        ),
        example=(
            "For every result, record the source asset and hash, metric contract, identifiers, "
            "timestamps, units, coverage, transformations, parameters, tool version, warnings, "
            "experimental unit, effective sample size, evidence links, limitations, and "
            "unresolved questions."
        ),
    ),
    "guardrails": _dvc_help(
        theory=(
            "Protect the decisions where plausible-looking output could still be unsafe or "
            "scientifically invalid. State the prohibited inference, the condition that "
            "triggers a stop, and the safe fallback or human decision needed."
        ),
        approaches=(
            "Hard stop: block work until a named prerequisite or approval exists.",
            "Narrowed fallback: permit descriptive work but prohibit stronger claims.",
            "Confirmation gate: pause before destructive, costly, sensitive, or external action.",
        ),
        example=(
            "Keep cage labels opaque and never invent missing metadata or silently exclude "
            "observations. Block or narrow the plan when the objective, experimental unit, "
            "group mapping, timezone, metric contract, or required approval is unresolved."
        ),
    ),
    "examples": _dvc_help(
        theory=(
            "Use examples as behavioral tests, not decoration. Cover an ordinary success, a "
            "near miss that should narrow or avoid triggering, and an edge case that exercises "
            "a guardrail."
        ),
        approaches=(
            "Scenario → expected behavior pairs.",
            "Representative input → acceptable output excerpts.",
            "Positive example paired with a counterexample and explanation.",
        ),
        example=(
            "Normal case: complete Type 1 and Type 2 exports with governed metadata are "
            "validated and analyzed. Near miss: with no verified timezone, run descriptive "
            "quality control but do not assign circadian phases. Edge case: if biological "
            "groups exist only in cage labels, do not infer them; request a governed mapping."
        ),
    ),
    "draft": _dvc_help(
        theory=(
            "Keep SKILL.md concise and operational: metadata controls discovery, while the body "
            "contains only non-obvious instructions needed after activation. OpenScientist "
            "currently transports only SKILL.md, so required behavior cannot depend on sibling "
            "reference or script files."
        ),
        approaches=(
            "Compact procedure: frontmatter followed by one end-to-end workflow.",
            "Decision guide: core rules followed by conditional branches and hard stops.",
            "Domain handbook: concise core workflow plus only the essential contracts and tables.",
        ),
        example=(
            "The real skill starts with frontmatter for name, description, category, slug, "
            "and tags, then uses operational sections such as Scientific role, Preconditions, "
            "Workflow, Analysis selection, Hard stops and approvals, and Evidence and report."
        ),
    ),
    "feedback": _dvc_help(
        theory=(
            "Request the smallest behavioral change that fixes a concrete problem. Name what "
            "is wrong, the desired rule or evidence, and how you will recognize a successful "
            "revision."
        ),
        approaches=(
            "Add: identify a missing constraint, example, or required output.",
            "Change: replace an ambiguous or over-broad instruction with a precise rule.",
            "Remove or relax: explain which constraint blocks valid alternative approaches.",
        ),
        example=(
            'Example revision request: "Distinguish vendor_rdi from legacy_udwa_rdi_proxy, '
            "and add a hard stop preventing equivalence claims until a versioned contract and "
            'golden-fixture conformance evidence exist." This identifies a concrete ambiguity '
            "and a testable acceptance condition."
        ),
    ),
}


def _format_field_help(field_key: str) -> str:
    """Format field theory, alternative patterns, and a source-traced example."""

    help_content = _FIELD_HELP[field_key]
    approaches = "\n".join(f"• {approach}" for approach in help_content.approaches)
    return (
        f"HOW TO WRITE IT\n{help_content.theory}\n\n"
        f"VALID PATTERNS\n{approaches}\n\n"
        f"REAL EXAMPLE — {help_content.skill_name}\n{help_content.example}\n\n"
        f"SOURCE\n{help_content.source_path}"
    )


def _add_real_skill_example(field: Any, field_key: str) -> None:
    """Add an accessible info control with writing guidance and a real example."""

    with field.add_slot("append"):
        with ui.button(icon="info_outline").props(
            "flat round dense color=primary "
            f"aria-label='Show writing guidance for {field_key.replace('_', ' ')}'"
        ):
            ui.tooltip(_format_field_help(field_key)).classes(
                "max-w-xl whitespace-pre-line text-sm leading-relaxed"
            )


def _has_errors(findings: tuple[SkillValidationFinding, ...]) -> bool:
    """Return whether deterministic validation found an export-blocking error."""

    return any(finding.severity == "error" for finding in findings)


def _brief_ready(brief: dict[str, str]) -> bool:
    """Require the two human-owned fields that define why and when to use a skill."""

    return bool(brief.get("purpose", "").strip() and brief.get("triggers", "").strip())


def _can_export(
    brief: dict[str, str],
    findings: tuple[SkillValidationFinding, ...],
    *,
    accepted: bool,
) -> bool:
    """Export requires the task contract, an exact accepted draft, and no errors."""

    return _brief_ready(brief) and accepted and not _has_errors(findings)


@ui.page("/skills/create")
@require_auth
async def skill_create_page() -> None:
    """Guide a user from a task contract to an explicitly accepted SKILL.md."""

    ui.page_title("Create Skill - OpenScientist")
    render_navigator(active_page="skills")

    state: dict[str, Any] = {
        "brief": {
            "purpose": "",
            "triggers": "",
            "inputs": "",
            "workflow": "",
            "outputs": "",
            "guardrails": "",
            "examples": "",
            "proposed_name": "",
            "category": "domain",
        },
        "draft": starter_skill_markdown(),
        "feedback": "",
        "assistant_message": (
            "Start with the task contract. You can edit the starter directly or ask the "
            "configured model to create a first draft."
        ),
        "questions": (),
        "findings": validate_skill_markdown(starter_skill_markdown()),
        "accepted": False,
        "busy": False,
    }

    with ui.column().classes("w-full max-w-screen-xl mx-auto gap-4"):
        with ui.row().classes("w-full items-start justify-between gap-4 flex-wrap"):
            with ui.column().classes("gap-1"):
                ui.label("Skill Creator").classes("text-h4 font-bold")
                ui.label(
                    "Define the task, collaborate with the model, review the evidence checks, "
                    "then explicitly accept and download SKILL.md."
                ).classes("text-gray-600 max-w-3xl")
            ui.button(
                "Back to Skills",
                icon="arrow_back",
                on_click=lambda: ui.navigate.to("/skills"),
            ).props("flat color=primary")

        with ui.expansion(
            "What makes a good skill?",
            caption="Six principles to use before drafting",
            icon="school",
            value=True,
        ).classes("w-full border rounded-lg bg-slate-50"):
            ui.label(
                "A skill is a compact onboarding guide for another capable agent. It should "
                "change behavior at the right moment, encode reusable judgment, and make "
                "success and safe failure observable."
            ).classes("text-sm text-gray-700 mb-3")
            with ui.grid(columns=2).classes("w-full gap-3"):
                for icon, title, description in _GOOD_SKILL_PRINCIPLES:
                    with ui.row().classes("items-start gap-2 no-wrap rounded border bg-white p-3"):
                        ui.icon(icon).classes("text-primary text-lg mt-0.5")
                        with ui.column().classes("gap-0"):
                            ui.label(title).classes("font-semibold text-sm")
                            ui.label(description).classes("text-xs text-gray-600 leading-relaxed")

        with ui.card().classes("w-full bg-blue-50"):
            with ui.row().classes("items-start gap-3 no-wrap"):
                ui.icon("verified_user").classes("text-primary text-xl")
                ui.label(
                    "Nothing is installed or published from this page. Model output is a "
                    "proposal; deterministic checks and your scientific review remain required."
                ).classes("text-sm text-gray-700")

        with ui.stepper().props("horizontal alternative-labels").classes("w-full") as stepper:
            with ui.step("Define", icon="assignment"):
                ui.label("Describe the task contract").classes("text-h6 font-semibold mb-1")
                ui.label(
                    "Concrete triggers, prerequisites, outputs, and stopping conditions give the "
                    "assistant much stronger constraints than a topic alone."
                ).classes("text-sm text-gray-600 mb-4")

                with ui.grid(columns=2).classes("w-full gap-4"):
                    name_input = ui.input(
                        "Proposed name",
                        placeholder="Replicate-aware differential expression",
                    ).classes("w-full")
                    _add_real_skill_example(name_input, "proposed_name")
                    category_input = ui.select(
                        {"domain": "Domain-specific", "workflow": "Cross-domain workflow"},
                        value="domain",
                        label="Category",
                    ).classes("w-full")

                purpose_input = ui.textarea(
                    "Purpose *",
                    placeholder="What repeated scientific decision or result should improve?",
                ).classes("w-full")
                _add_real_skill_example(purpose_input, "purpose")
                triggers_input = ui.textarea(
                    "Triggers and non-triggers *",
                    placeholder=("Use when... Include two concrete examples. Do not use when..."),
                ).classes("w-full")
                _add_real_skill_example(triggers_input, "triggers")
                inputs_input = ui.textarea(
                    "Inputs and prerequisites",
                    placeholder="Required data, metadata, tools, assumptions, and missing-input behavior",
                ).classes("w-full")
                _add_real_skill_example(inputs_input, "inputs")
                workflow_input = ui.textarea(
                    "Required workflow",
                    placeholder="Ordered decisions, checks, methods, and completion criteria",
                ).classes("w-full")
                _add_real_skill_example(workflow_input, "workflow")
                outputs_input = ui.textarea(
                    "Output contract",
                    placeholder="Evidence, provenance, uncertainty, and deliverables to record",
                ).classes("w-full")
                _add_real_skill_example(outputs_input, "outputs")
                guardrails_input = ui.textarea(
                    "Guardrails and human checkpoints",
                    placeholder=(
                        "Unsupported claims, failure behavior, and actions requiring confirmation"
                    ),
                ).classes("w-full")
                _add_real_skill_example(guardrails_input, "guardrails")
                examples_input = ui.textarea(
                    "Examples and evaluation cases",
                    placeholder="A normal case, a near miss, an edge case, and the expected behavior",
                ).classes("w-full")
                _add_real_skill_example(examples_input, "examples")

                for field in (
                    purpose_input,
                    triggers_input,
                    inputs_input,
                    workflow_input,
                    outputs_input,
                    guardrails_input,
                    examples_input,
                ):
                    field.props("outlined autogrow")
                name_input.props("outlined")
                category_input.props("outlined")

                def sync_brief() -> None:
                    state["brief"] = {
                        "purpose": str(purpose_input.value or "").strip(),
                        "triggers": str(triggers_input.value or "").strip(),
                        "inputs": str(inputs_input.value or "").strip(),
                        "workflow": str(workflow_input.value or "").strip(),
                        "outputs": str(outputs_input.value or "").strip(),
                        "guardrails": str(guardrails_input.value or "").strip(),
                        "examples": str(examples_input.value or "").strip(),
                        "proposed_name": str(name_input.value or "").strip(),
                        "category": str(category_input.value or "domain"),
                    }
                    state["accepted"] = False

                def continue_to_review() -> None:
                    sync_brief()
                    stepper.next()

                with ui.stepper_navigation():
                    ui.button(
                        "Review draft",
                        icon="arrow_forward",
                        on_click=continue_to_review,
                    )

            with ui.step("Review", icon="rate_review"):
                with ui.grid(columns=2).classes("w-full gap-4 items-start"):
                    with ui.column().classes("w-full gap-3"):
                        ui.label("Editable SKILL.md").classes("text-h6 font-semibold")
                        draft_input = ui.textarea(value=state["draft"]).classes(
                            "w-full font-mono text-sm"
                        )
                        _add_real_skill_example(draft_input, "draft")
                        draft_input.props("outlined autogrow input-style='min-height: 34rem'")

                        feedback_input = ui.textarea(
                            "Feedback for the next model revision",
                            placeholder=(
                                "Tell the assistant what is wrong, missing, too strict, or "
                                "scientifically unsupported."
                            ),
                        ).classes("w-full")
                        _add_real_skill_example(feedback_input, "feedback")
                        feedback_input.props("outlined autogrow")

                        with ui.row().classes("gap-2 flex-wrap"):
                            generate_button = ui.button("Generate with AI", icon="auto_awesome")
                            validate_button = ui.button("Run checks", icon="fact_check").props(
                                "outline"
                            )
                            ui.button(
                                "Back to task contract",
                                icon="arrow_back",
                                on_click=stepper.previous,
                            ).props("flat")

                    with ui.column().classes("w-full gap-3"):
                        ui.label("Review evidence").classes("text-h6 font-semibold")

                        @ui.refreshable
                        def render_review() -> None:
                            with ui.card().classes("w-full"):
                                ui.label(state["assistant_message"]).classes("text-gray-700")
                                if state["questions"]:
                                    ui.separator()
                                    ui.label("Questions for you").classes(
                                        "font-semibold text-gray-700"
                                    )
                                    for question in state["questions"]:
                                        with ui.row().classes("items-start gap-2 no-wrap"):
                                            ui.icon("help_outline").classes("text-primary")
                                            ui.label(question).classes("text-sm")

                            findings = state["findings"]
                            errors = sum(item.severity == "error" for item in findings)
                            warnings = sum(item.severity == "warning" for item in findings)
                            with ui.row().classes("gap-2"):
                                ui.badge(f"{errors} errors", color="negative").props("outline")
                                ui.badge(f"{warnings} warnings", color="warning").props("outline")
                                ui.badge(f"{len(findings)} checks", color="info").props("outline")

                            if not findings:
                                with ui.card().classes("w-full bg-green-50"):
                                    ui.label(
                                        "No deterministic issues found. Scientific review is "
                                        "still required."
                                    ).classes("text-green-800")
                            for finding in findings:
                                with ui.card().classes("w-full"):
                                    with ui.row().classes("items-start gap-2 no-wrap"):
                                        ui.icon(_SEVERITY_ICON[finding.severity]).classes(
                                            f"text-{_SEVERITY_COLOR[finding.severity]}"
                                        )
                                        with ui.column().classes("gap-0"):
                                            ui.label(finding.code).classes(
                                                "font-mono text-xs text-gray-500"
                                            )
                                            ui.label(finding.message).classes("text-sm")

                            with ui.expansion("Rendered preview", icon="preview").classes("w-full"):
                                ui.markdown(state["draft"]).classes("w-full skill-content")

                        render_review()

                def apply_manual_draft() -> None:
                    state["draft"] = str(draft_input.value or "")
                    state["findings"] = validate_skill_markdown(state["draft"])
                    state["accepted"] = False
                    state["questions"] = ()
                    state["assistant_message"] = (
                        "Manual edits checked. Review each finding before accepting the draft."
                    )
                    render_review.refresh()

                async def generate() -> None:
                    sync_brief()
                    if not state["brief"]["purpose"] or not state["brief"]["triggers"]:
                        ui.notify(
                            "Add a purpose and concrete triggers before generating.",
                            type="warning",
                        )
                        return

                    guard = ClientGuard()
                    state["busy"] = True
                    generate_button.disable()
                    generate_button.props("loading")
                    try:
                        request = SkillDraftRequest(
                            brief=SkillAuthoringBrief(**state["brief"]),
                            current_draft=str(draft_input.value or ""),
                            feedback=str(feedback_input.value or ""),
                        )
                        result = await generate_skill_draft(request)
                        if not guard.is_connected:
                            return
                        state["draft"] = result.draft_markdown
                        state["assistant_message"] = result.assistant_message
                        state["questions"] = result.questions
                        state["findings"] = result.findings
                        state["accepted"] = False
                        draft_input.value = result.draft_markdown
                        feedback_input.value = ""
                        render_review.refresh()
                        ui.notify("Draft updated. Review it before accepting.", type="positive")
                    except Exception as exc:
                        logger.error("Skill generation failed: %s", exc, exc_info=True)
                        if guard.is_connected:
                            detail = str(exc).strip() or "The provider returned an unknown error."
                            ui.notify(
                                f"Draft preserved: {detail[:240]}",
                                type="negative",
                            )
                    finally:
                        state["busy"] = False
                        if guard.is_connected:
                            generate_button.enable()
                            generate_button.props(remove="loading")

                generate_button.on_click(generate)
                validate_button.on_click(apply_manual_draft)

                def on_draft_change(event: Any) -> None:
                    state["draft"] = str(get_event_value(event) or "")
                    state["accepted"] = False
                    state["findings"] = validate_skill_markdown(state["draft"])
                    render_review.refresh()

                draft_input.on("change", on_draft_change)

                def continue_to_acceptance() -> None:
                    apply_manual_draft()
                    render_acceptance.refresh()
                    stepper.next()

                with ui.stepper_navigation():
                    ui.button(
                        "Acceptance",
                        icon="arrow_forward",
                        on_click=continue_to_acceptance,
                    )

            with ui.step("Accept and export", icon="download"):
                ui.label("Human acceptance").classes("text-h6 font-semibold")
                ui.label(
                    "Confirm that you reviewed the scientific claims, tool assumptions, failure "
                    "behavior, and deterministic findings. Acceptance is invalidated by any edit."
                ).classes("text-gray-600 max-w-3xl")

                @ui.refreshable
                def render_acceptance() -> None:
                    findings = state["findings"]
                    if not _brief_ready(state["brief"]):
                        with ui.card().classes("w-full bg-red-50"):
                            ui.label(
                                "Return to Define and add both a purpose and concrete trigger/"
                                "non-trigger cases before acceptance."
                            ).classes("text-red-800")
                    elif _has_errors(findings):
                        with ui.card().classes("w-full bg-red-50"):
                            ui.label(
                                "Export is blocked by deterministic errors. Return to Review and "
                                "fix them."
                            ).classes("text-red-800")
                    elif state["accepted"]:
                        with ui.card().classes("w-full bg-green-50"):
                            ui.label(
                                "Accepted for export. This records only your current page state; "
                                "it does not approve or install the skill in OpenScientist."
                            ).classes("text-green-800")
                    else:
                        with ui.card().classes("w-full bg-amber-50"):
                            ui.label(
                                "The draft has no blocking syntax errors, but it is not yet "
                                "accepted by you."
                            ).classes("text-amber-900")

                render_acceptance()

                def accept_draft() -> None:
                    state["findings"] = validate_skill_markdown(state["draft"])
                    if not _brief_ready(state["brief"]):
                        render_acceptance.refresh()
                        ui.notify(
                            "Add a purpose and concrete triggers before accepting.",
                            type="negative",
                        )
                        return
                    if _has_errors(state["findings"]):
                        render_acceptance.refresh()
                        ui.notify("Fix blocking errors before accepting.", type="negative")
                        return
                    state["accepted"] = True
                    render_acceptance.refresh()
                    ui.notify("Draft accepted for download.", type="positive")

                def download_draft() -> None:
                    if not _can_export(
                        state["brief"],
                        state["findings"],
                        accepted=state["accepted"],
                    ):
                        ui.notify("Accept an error-free draft first.", type="warning")
                        return
                    ui.download(state["draft"].encode("utf-8"), filename="SKILL.md")

                with ui.row().classes("gap-2 flex-wrap"):
                    ui.button("Accept this draft", icon="task_alt", on_click=accept_draft)
                    ui.button("Download SKILL.md", icon="download", on_click=download_draft).props(
                        "outline"
                    )
                    ui.button(
                        "Return to review", icon="arrow_back", on_click=stepper.previous
                    ).props("flat")
