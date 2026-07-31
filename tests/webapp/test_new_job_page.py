"""Tests for new job page helpers."""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openscientist.job_brief_assistant import JobBriefSuggestion
from openscientist.webapp_components.pages import new_job
from openscientist.webapp_components.pages.new_job import (
    _apply_job_brief_suggestion,
    _build_upload_session_id,
    _submit_job,
)


def test_build_upload_session_id_uses_user_and_client_id():
    """Upload session IDs should be scoped by user and websocket client."""
    client = SimpleNamespace(id="client-abc")
    session_id = _build_upload_session_id("user-123", client)
    assert session_id == "user-123:client-abc"


def test_build_upload_session_id_handles_missing_user_with_anonymous_prefix():
    """Anonymous fallback should still include client identity."""
    client = SimpleNamespace(id="client-xyz")
    session_id = _build_upload_session_id(None, client)
    assert session_id == "anonymous:client-xyz"


def test_submit_job_has_use_hypotheses_parameter():
    """_submit_job must accept use_hypotheses so the form toggle is wired in."""
    sig = inspect.signature(_submit_job)
    assert "use_hypotheses" in sig.parameters


def test_submit_job_has_coinvestigate_mode_at_top_level():
    """coinvestigate_mode must be a top-level parameter of _submit_job."""
    sig = inspect.signature(_submit_job)
    assert "coinvestigate_mode" in sig.parameters


def test_submit_job_has_description_parameter():
    """The optional study context field must be wired into submission."""
    sig = inspect.signature(_submit_job)
    assert "description" in sig.parameters


def test_apply_job_brief_suggestion_updates_both_controls():
    """AI suggestions remain inert until explicitly applied to both fields."""
    question = SimpleNamespace(value="Old question", update=MagicMock())
    description = SimpleNamespace(value="Old context", update=MagicMock())

    _apply_job_brief_suggestion(
        question,
        description,
        JobBriefSuggestion(
            research_question="Improved question",
            description="Improved context",
        ),
    )

    assert question.value == "Improved question"
    assert description.value == "Improved context"
    question.update.assert_called_once_with()
    description.update.assert_called_once_with()


def test_submit_job_passes_trimmed_description_to_job_manager():
    """Study context should use the existing JobManager description field."""
    job_manager = MagicMock()
    controls = {
        "research_question": SimpleNamespace(value="What differs between groups?"),
        "description": SimpleNamespace(value="  Cage is the experimental unit.  "),
        "max_iterations": SimpleNamespace(value=5),
        "use_hypotheses": SimpleNamespace(value=False),
        "coinvestigate_mode": SimpleNamespace(value=False),
    }

    with (
        patch.object(new_job, "get_current_user_id", return_value="user-123"),
        patch.object(new_job, "_persist_uploaded_files", return_value=[]),
        patch.object(new_job, "clear_uploaded_files"),
        patch.object(new_job.ui, "notify"),
        patch.object(new_job.ui.navigate, "to"),
    ):
        _submit_job(
            job_manager=job_manager,
            user_can_start_jobs=True,
            session_id="session-123",
            **controls,
        )

    assert job_manager.create_job.call_args.kwargs["description"] == (
        "Cage is the experimental unit."
    )


def test_submit_job_accepts_reviewed_evidence_plan():
    """The form must pass the approved preflight contract to job creation."""
    sig = inspect.signature(_submit_job)
    assert "evidence_librarian_enabled" in sig.parameters
    assert "evidence_plan" in sig.parameters
