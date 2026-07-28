"""New job submission page."""

import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

from nicegui import ui

from openscientist.auth import can_current_user_start_jobs, get_current_user_id, require_auth
from openscientist.evidence_librarian import (
    EvidencePlan,
    approve_evidence_plan,
    build_evidence_plan_from_enabled_skills,
    evidence_plan_from_dict,
    evidence_plan_to_dict,
    select_plan_skills,
)
from openscientist.providers import check_provider_config
from openscientist.webapp_components.ui_components import (
    render_config_error_banner,
    render_navigator,
    render_pending_approval_notice,
)
from openscientist.webapp_components.utils import get_event_value
from openscientist.webapp_components.utils.session import (
    add_uploaded_file,
    clear_uploaded_files,
    get_uploaded_files,
)

logger = logging.getLogger(__name__)


def _build_upload_session_id(user_id: str | None, client: object) -> str:
    """Build an upload-session key scoped to user and websocket client."""
    effective_user_id = user_id or "anonymous"
    client_id = str(getattr(client, "id", id(client)))
    return f"{effective_user_id}:{client_id}"


def _persist_uploaded_files(session_id: str) -> list[Path]:
    """Return temp file paths for all uploaded files in the session."""
    return [uploaded_file["path"] for uploaded_file in get_uploaded_files(session_id)]


def _notify_creation_error(error: Exception) -> None:
    """Show user-friendly notification for job creation failures."""
    error_msg = str(error).lower()
    if "authentication" in error_msg or "api key" in error_msg:
        ui.notify(
            "Authentication error. Please contact your administrator to check API credentials.",
            type="negative",
        )
        return
    if "event loop" in error_msg:
        ui.notify("Internal server error. Please try again or contact support.", type="negative")
        return
    ui.notify("Error creating job. Please try again or contact support.", type="negative")


def _submit_job(
    *,
    job_manager: Any,
    user_can_start_jobs: bool,
    session_id: str,
    research_question: ui.textarea,
    max_iterations: ui.number,
    use_hypotheses: ui.switch,
    coinvestigate_mode: ui.switch,
    evidence_librarian_enabled: bool = False,
    evidence_plan: dict[str, Any] | None = None,
) -> None:
    """Validate input and create a new discovery job."""
    if not user_can_start_jobs:
        ui.notify("Your account is pending administrator approval.", type="warning")
        return

    question = research_question.value.strip()
    if not question:
        ui.notify("Please enter a research question", type="negative")
        return

    current_user_id = get_current_user_id()
    if not current_user_id:
        ui.notify("Authentication required. Please log in again.", type="negative")
        ui.navigate.to("/login")
        return

    job_id = str(uuid.uuid4())
    data_files = _persist_uploaded_files(session_id)
    mode = "coinvestigate" if coinvestigate_mode.value else "autonomous"

    try:
        if evidence_librarian_enabled:
            if evidence_plan is None:
                ui.notify(
                    "Prepare and approve the Evidence Librarian plan before starting.",
                    type="warning",
                )
                return
            parsed_plan = evidence_plan_from_dict(evidence_plan)
            current_files = tuple(path.name for path in data_files)
            if (
                parsed_plan.status != "approved"
                or parsed_plan.research_question != question
                or parsed_plan.data_files != current_files
            ):
                ui.notify(
                    "The evidence plan is stale. Prepare and approve it again.",
                    type="warning",
                )
                return
        job_manager.create_job(
            job_id=job_id,
            research_question=question,
            data_files=data_files,
            max_iterations=int(max_iterations.value),
            use_hypotheses=use_hypotheses.value,
            auto_start=True,
            investigation_mode=mode,
            owner_id=current_user_id,
            evidence_plan=evidence_plan if evidence_librarian_enabled else None,
        )
        ui.notify(f"Job {job_id} created and started!", type="positive")
        clear_uploaded_files(session_id)
        ui.navigate.to(f"/job/{job_id}")
    except Exception as exc:
        logger.error("Error creating job: %s", exc, exc_info=True)
        _notify_creation_error(exc)


async def _handle_upload(e: Any, session_id: str) -> None:
    """Stream upload directly to a temp file and record its path in session state."""
    try:
        name = e.file.name
        temp_path = Path(tempfile.mkdtemp()) / name
        await e.file.save(temp_path)
        add_uploaded_file(session_id, name, temp_path)
        ui.notify(f"Uploaded: {name}", type="positive")
        logger.info("Successfully uploaded %s (%d bytes)", name, e.file.size())
    except (ValueError, OSError) as exc:
        logger.error("Upload failed: %s", exc, exc_info=True)
        ui.notify(f"Upload failed: {exc}", type="negative")


@ui.page("/new")
@require_auth
def new_job_page() -> None:
    """Job submission form."""
    from openscientist import web_app

    job_manager = web_app.get_job_manager()
    user_can_start_jobs = can_current_user_start_jobs()
    user_id = get_current_user_id()
    client = ui.context.client
    session_id = _build_upload_session_id(user_id, client)
    client.on_disconnect(lambda: clear_uploaded_files(session_id))

    is_configured, provider_name, config_errors = check_provider_config()
    render_navigator(active_page="new", show_new_job=is_configured)

    if not user_can_start_jobs:
        render_pending_approval_notice()
        ui.button("Back to Jobs", on_click=lambda: ui.navigate.to("/jobs")).props(
            "outline color=primary"
        ).classes("mt-4")
        return

    if not is_configured:
        render_config_error_banner(provider_name, config_errors, show_back_button=True)
        return

    async def on_upload(event: Any) -> None:
        await _handle_upload(event, session_id)

    with ui.card().classes("w-full max-w-2xl mx-auto mt-8"):
        ui.label("Submit Discovery Job").classes("text-h5 mb-4")

        research_question = ui.textarea(
            label="Research Question",
            placeholder="e.g., What metabolic pathways are affected by hypothermia?",
            validation={"Too short": lambda value: len(value) >= 10},
        ).classes("w-full")

        ui.upload(
            label="Upload Data Files (Optional - Tabular, Structures, Sequences, Images)",
            multiple=True,
            auto_upload=True,
            on_upload=on_upload,
        ).classes("w-full")
        ui.label("Maximum file size: 500 MB per file").classes("text-caption text-grey-6")

        max_iterations = ui.number(
            label="Max Iterations",
            value=10,
            min=2,
            max=100,
            step=1,
        ).classes("w-full")

        ui.separator().classes("my-4")
        use_hypotheses = ui.switch("Hypothesis Generation", value=False)
        ui.label(
            "Track scientific hypotheses across iterations — propose, test, and confirm/reject them."
        ).classes("text-sm text-gray-700 mt-1")

        ui.separator().classes("my-4")
        coinvestigate_mode = ui.switch("Coinvestigate Mode", value=False)
        ui.label(
            "Requires your active participation. After each iteration, I will pause to receive your feedback."
        ).classes("text-sm text-gray-700 mt-1")
        ui.label(
            "Requires you to stay near your computer. Auto-continues after 15 min if you don't respond."
        ).classes("text-xs text-orange-700")

        ui.separator().classes("my-4")
        evidence_librarian = ui.switch("Evidence Librarian", value=True)
        ui.label(
            "Compose a job-specific skill bundle and literature-search plan. "
            "You must review and approve it before the job starts."
        ).classes("text-sm text-gray-700 mt-1")

        librarian_state: dict[str, Any] = {
            "plan": None,
            "selected_keys": set(),
            "approved_plan": None,
            "busy": False,
        }

        def _toggle_candidate(key: str, selected: bool) -> None:
            if selected:
                librarian_state["selected_keys"].add(key)
            else:
                librarian_state["selected_keys"].discard(key)
            librarian_state["approved_plan"] = None
            render_evidence_plan.refresh()

        def _approve_plan() -> None:
            plan: EvidencePlan | None = librarian_state["plan"]
            if plan is None or not user_id:
                ui.notify("Prepare a plan before approving it.", type="warning")
                return
            selected_plan = select_plan_skills(plan, librarian_state["selected_keys"])
            approved = approve_evidence_plan(selected_plan, user_id)
            librarian_state["plan"] = approved
            librarian_state["approved_plan"] = evidence_plan_to_dict(approved)
            render_evidence_plan.refresh()
            ui.notify("Evidence plan approved for this job.", type="positive")

        async def _prepare_plan() -> None:
            question = str(research_question.value or "").strip()
            if len(question) < 10:
                ui.notify("Enter a research question before preparing the plan.", type="warning")
                return
            librarian_state["busy"] = True
            render_evidence_plan.refresh()
            try:
                files = _persist_uploaded_files(session_id)
                plan = await build_evidence_plan_from_enabled_skills(question, files)
                librarian_state["plan"] = plan
                librarian_state["selected_keys"] = set(plan.selected_skill_keys)
                librarian_state["approved_plan"] = None
                ui.notify("Draft evidence plan is ready for review.", type="positive")
            except Exception as exc:
                logger.error("Evidence plan preparation failed: %s", exc, exc_info=True)
                ui.notify(f"Could not prepare evidence plan: {exc}", type="negative")
            finally:
                librarian_state["busy"] = False
                render_evidence_plan.refresh()

        @ui.refreshable
        def render_evidence_plan() -> None:
            plan: EvidencePlan | None = librarian_state["plan"]
            approved = librarian_state["approved_plan"] is not None
            with ui.card().classes("w-full mt-3 bg-grey-1"):
                if librarian_state["busy"]:
                    with ui.row().classes("items-center gap-2"):
                        ui.spinner(size="sm")
                        ui.label("Preparing skill and bibliographic recommendations…")
                    return
                if plan is None:
                    ui.label(
                        "No plan prepared. The job cannot start with the Librarian enabled "
                        "until you approve a plan."
                    ).classes("text-sm text-gray-600")
                    return

                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("Evidence and skill plan").classes("font-semibold")
                    ui.badge(
                        "Approved" if approved else "Review required",
                        color="positive" if approved else "warning",
                    )

                ui.label("Proposed skill bundle").classes("text-sm font-semibold mt-2")
                if not plan.candidates:
                    ui.label("No enabled skills are available.").classes("text-sm text-gray-600")
                for candidate in plan.candidates:
                    mandatory = candidate.category == "workflow"
                    checked = mandatory or candidate.key in librarian_state["selected_keys"]
                    with ui.row().classes("w-full items-start gap-2 no-wrap"):
                        selector = ui.checkbox(
                            candidate.name,
                            value=checked,
                            on_change=lambda event, key=candidate.key: _toggle_candidate(
                                key, bool(get_event_value(event))
                            ),
                        )
                        if mandatory:
                            selector.disable()
                        with ui.column().classes("gap-0"):
                            ui.label(f"{candidate.key} · score {candidate.score:g}").classes(
                                "text-xs text-gray-500"
                            )
                            ui.label("; ".join(candidate.reasons)).classes("text-xs text-gray-600")

                ui.label("Planned PubMed queries").classes("text-sm font-semibold mt-3")
                for query in plan.literature_queries:
                    ui.label(f"• {query}").classes("text-xs text-gray-700")

                with ui.row().classes("gap-2 mt-3"):
                    ui.button(
                        "Approve plan",
                        icon="verified_user",
                        on_click=_approve_plan,
                    ).props("color=positive")
                    ui.button(
                        "Rebuild",
                        icon="refresh",
                        on_click=_prepare_plan,
                    ).props("flat")

        ui.button(
            "Prepare evidence plan",
            icon="local_library",
            on_click=_prepare_plan,
        ).props("outline").classes("mt-2")
        render_evidence_plan()

        ui.button(
            "Start Discovery",
            on_click=lambda: _submit_job(
                job_manager=job_manager,
                user_can_start_jobs=user_can_start_jobs,
                session_id=session_id,
                research_question=research_question,
                max_iterations=max_iterations,
                use_hypotheses=use_hypotheses,
                coinvestigate_mode=coinvestigate_mode,
                evidence_librarian_enabled=bool(evidence_librarian.value),
                evidence_plan=librarian_state["approved_plan"],
            ),
        ).classes("w-full mt-4")
