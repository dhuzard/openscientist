"""Tests for job-detail action gating (admin "Regenerate report")."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from openscientist.job.types import JobStatus
from openscientist.webapp_components.pages import job_detail


def _context(status: JobStatus) -> SimpleNamespace:
    """Minimal stand-in carrying only what _can_regenerate_report reads."""
    return SimpleNamespace(job_info=SimpleNamespace(status=status), can_edit=True)


def _action_context() -> SimpleNamespace:
    """Stand-in for the regenerate action handler with a recording manager."""
    return SimpleNamespace(
        job_id="job-1",
        job_manager=SimpleNamespace(regenerate_report=MagicMock()),
    )


def _restart_context() -> SimpleNamespace:
    return SimpleNamespace(
        job_id="00000000-0000-0000-0000-000000000001",
        job_manager=SimpleNamespace(restart_job=MagicMock()),
    )


@pytest.mark.asyncio
async def test_load_job_cost_records_applies_user_rls_and_returns_turns() -> None:
    job_id = uuid4()
    user_id = uuid4()
    turns = [SimpleNamespace(model="gpt-5.5")]
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = turns
    session = AsyncMock()
    session.execute.return_value = execute_result
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=None)

    with (
        patch.object(job_detail, "get_thread_safe_session_ctx", return_value=session_context),
        patch.object(job_detail, "set_current_user", new=AsyncMock()) as set_user,
    ):
        result = await job_detail._load_job_cost_records(str(job_id), str(user_id))

    assert result == turns
    set_user.assert_awaited_once_with(session, user_id)
    session.execute.assert_awaited_once()


def test_load_job_skill_usage_distinguishes_assignment_snapshot(tmp_path: Path) -> None:
    without_snapshot = job_detail._load_job_skill_usage(tmp_path)
    assert without_snapshot["assignment_snapshot_available"] is False
    assert without_snapshot["assigned_skills"] == []

    manifest = [
        {
            "id": str(uuid4()),
            "key": "analysis--profile",
            "name": "Profile analysis",
            "category": "analysis",
            "slug": "profile",
            "version": 1,
        }
    ]
    (tmp_path / ".openscientist_skill_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with_snapshot = job_detail._load_job_skill_usage(tmp_path)
    assert with_snapshot["assignment_snapshot_available"] is True
    assert with_snapshot["assigned_skills"] == manifest


def test_agentic_and_scientific_reporting_have_separate_tabs() -> None:
    tabs_source = inspect.getsource(job_detail._render_job_tabs)
    agentic_source = inspect.getsource(job_detail._render_agentic_info_tab)
    report_source = inspect.getsource(job_detail._render_report_tab)

    assert 'ui.tab("Agentic Info"' in tabs_source
    assert 'ui.tab("Scientific Report"' in tabs_source
    assert "_render_agentic_info_tab(context)" in tabs_source
    assert "_render_report_tab(context)" in tabs_source

    for renderer in (
        "_render_job_agent_task_trace",
        "_render_job_skill_usage",
        "_render_job_agent_usage",
    ):
        assert renderer in agentic_source
        assert renderer not in report_source

    assert "final_report.md" not in agentic_source
    assert "final_report.md" in report_source


@pytest.mark.parametrize(
    "is_admin,status,expected",
    [
        (True, JobStatus.COMPLETED, True),  # admin + completed -> shown
        (False, JobStatus.COMPLETED, False),  # non-admin -> hidden
        (True, JobStatus.RUNNING, False),  # not completed -> hidden
        (True, JobStatus.FAILED, False),  # not completed -> hidden
        (False, JobStatus.RUNNING, False),  # neither -> hidden
    ],
)
def test_can_regenerate_report_gating(is_admin: bool, status: JobStatus, expected: bool) -> None:
    with patch.object(job_detail, "is_current_user_admin", return_value=is_admin):
        assert job_detail._can_regenerate_report(_context(status)) is expected  # type: ignore[arg-type]


def test_regenerate_action_blocks_non_admin_server_side() -> None:
    """The action handler re-checks admin and never reaches the manager for a
    non-admin, even if the button were somehow triggered."""
    context = _action_context()
    with (
        patch.object(job_detail, "is_current_user_admin", return_value=False),
        patch.object(job_detail, "ui"),  # swallow notify/navigate
    ):
        job_detail._regenerate_report(context)  # type: ignore[arg-type]
    context.job_manager.regenerate_report.assert_not_called()


def test_regenerate_action_calls_manager_for_admin() -> None:
    """An admin action reaches the manager with the job id."""
    context = _action_context()
    with (
        patch.object(job_detail, "is_current_user_admin", return_value=True),
        patch.object(job_detail, "ui"),  # swallow notify/navigate
    ):
        job_detail._regenerate_report(context)  # type: ignore[arg-type]
    context.job_manager.regenerate_report.assert_called_once_with("job-1")


@pytest.mark.parametrize(
    "can_edit,can_start,status,expected",
    [
        (True, True, JobStatus.FAILED, True),
        (True, True, JobStatus.CANCELLED, True),
        (False, True, JobStatus.FAILED, False),
        (True, False, JobStatus.CANCELLED, False),
        (True, True, JobStatus.COMPLETED, False),
        (True, True, JobStatus.RUNNING, False),
    ],
)
def test_can_restart_job_gating(
    can_edit: bool,
    can_start: bool,
    status: JobStatus,
    expected: bool,
) -> None:
    context = SimpleNamespace(
        can_edit=can_edit,
        job_info=SimpleNamespace(status=status),
    )
    with patch.object(
        job_detail,
        "can_current_user_start_jobs",
        return_value=can_start,
    ):
        assert job_detail._can_restart_job(context) is expected  # type: ignore[arg-type]


def test_restart_action_rechecks_permission_and_calls_manager() -> None:
    context = _restart_context()
    with (
        patch.object(job_detail, "get_current_user_id", return_value=str(uuid4())),
        patch.object(job_detail, "can_current_user_start_jobs", return_value=True),
        patch.object(job_detail, "_load_db_job_for_user", return_value=SimpleNamespace()),
        patch.object(job_detail, "_resolve_job_permissions", return_value=(True, True)),
        patch.object(job_detail, "ui"),
    ):
        job_detail._restart_job(context)  # type: ignore[arg-type]

    context.job_manager.restart_job.assert_called_once_with(context.job_id)


def test_restart_action_blocks_when_edit_permission_was_revoked() -> None:
    context = _restart_context()
    with (
        patch.object(job_detail, "get_current_user_id", return_value=str(uuid4())),
        patch.object(job_detail, "can_current_user_start_jobs", return_value=True),
        patch.object(job_detail, "_load_db_job_for_user", return_value=SimpleNamespace()),
        patch.object(job_detail, "_resolve_job_permissions", return_value=(False, False)),
        patch.object(job_detail, "ui"),
    ):
        job_detail._restart_job(context)  # type: ignore[arg-type]

    context.job_manager.restart_job.assert_not_called()


@pytest.mark.parametrize(
    "status,expected",
    [
        (
            JobStatus.RUNNING,
            {"add_idea", "pause", "reduce_iterations", "stop_and_report"},
        ),
        (JobStatus.AWAITING_FEEDBACK, {"pause", "reduce_iterations", "stop_and_report"}),
        (JobStatus.PAUSED, {"resume", "reduce_iterations", "stop_and_report"}),
        (JobStatus.COMPLETED, set()),
    ],
)
def test_owner_run_controls_follow_job_status(
    status: JobStatus,
    expected: set[str],
) -> None:
    context = SimpleNamespace(
        is_owner=True,
        job_info=SimpleNamespace(
            status=status,
            iterations_completed=2,
            max_iterations=10,
        ),
    )
    assert job_detail._available_run_controls(context) == expected  # type: ignore[arg-type]


def test_shared_user_cannot_see_run_controls() -> None:
    context = SimpleNamespace(
        is_owner=False,
        job_info=SimpleNamespace(
            status=JobStatus.RUNNING,
            iterations_completed=2,
            max_iterations=10,
        ),
    )
    assert job_detail._available_run_controls(context) == set()  # type: ignore[arg-type]


def test_add_idea_hidden_when_current_iteration_is_final() -> None:
    context = SimpleNamespace(
        is_owner=True,
        job_info=SimpleNamespace(
            status=JobStatus.RUNNING,
            iterations_completed=4,
            max_iterations=5,
        ),
    )
    assert "add_idea" not in job_detail._available_run_controls(context)  # type: ignore[arg-type]


def test_queue_job_idea_persists_without_lifecycle_transition() -> None:
    owner_id = uuid4()
    queued_call = object()
    context = SimpleNamespace(
        is_owner=True,
        job_id="00000000-0000-0000-0000-000000000001",
        job_manager=SimpleNamespace(),
    )
    queue_guidance = MagicMock(return_value=queued_call)
    with (
        patch.object(job_detail, "get_current_user_id", return_value=str(owner_id)),
        patch.object(job_detail, "queue_job_guidance", new=queue_guidance),
        patch.object(job_detail, "run_sync") as run_sync_mock,
        patch.object(job_detail, "ui") as ui_mock,
    ):
        result = job_detail._queue_job_idea(context, "Inspect activity timing.")  # type: ignore[arg-type]

    assert result is True
    queue_guidance.assert_called_once_with(
        context.job_id,
        owner_id,
        "Inspect activity timing.",
    )
    run_sync_mock.assert_called_once_with(queued_call)
    ui_mock.notify.assert_called_once_with(
        "Idea queued for the next iteration.",
        type="positive",
    )
    assert vars(context.job_manager) == {}


def test_queue_job_idea_rejects_non_owner_before_persistence() -> None:
    context = SimpleNamespace(is_owner=False, job_id="job-1")
    with (
        patch.object(job_detail, "queue_job_guidance") as queue_guidance,
        patch.object(job_detail, "run_sync") as run_sync_mock,
        patch.object(job_detail, "ui") as ui_mock,
    ):
        result = job_detail._queue_job_idea(context, "A new direction")  # type: ignore[arg-type]

    assert result is False
    queue_guidance.assert_not_called()
    run_sync_mock.assert_not_called()
    ui_mock.notify.assert_called_once_with(
        "Only the job owner can add ideas to this run.",
        type="negative",
    )


def test_owner_action_calls_requested_manager_method() -> None:
    manager = SimpleNamespace(pause_job=MagicMock())
    context = SimpleNamespace(is_owner=True, job_id="job-1", job_manager=manager)
    with patch.object(job_detail, "ui"):
        job_detail._run_owner_job_action(
            context,  # type: ignore[arg-type]
            "pause_job",
            "Paused",
        )
    manager.pause_job.assert_called_once_with("job-1")


class TestTimelineHeaderText:
    """The per-iteration timeline header must not mislabel a summary-less
    iteration as 'Completed' (which reads as the whole job being done)."""

    def test_strapline_wins(self) -> None:
        assert job_detail._timeline_header_text("Found X", "long summary", False) == "Found X"

    def test_summary_used_when_no_strapline(self) -> None:
        assert job_detail._timeline_header_text("", "Did Y", False) == "Did Y"

    def test_in_progress_without_summary(self) -> None:
        assert job_detail._timeline_header_text("", "", True) == "Investigation in progress..."

    def test_no_summary_no_activity_is_not_labelled_completed(self) -> None:
        # The regression: a finished iteration with no summary used to say
        # "Completed". With no activity either, say so plainly.
        out = job_detail._timeline_header_text("", "", False, has_activity=False)
        assert out == "No activity or summary recorded"
        assert out != "Completed"

    def test_activity_without_summary_is_distinguished(self) -> None:
        # An iteration that did work but skipped save_iteration_summary must not
        # read as idle (and must not say "Completed").
        out = job_detail._timeline_header_text("", "", False, has_activity=True)
        assert out == "Activity logged, but no summary recorded"
        assert out != "Completed"

    def test_in_progress_suffix_on_strapline(self) -> None:
        assert job_detail._timeline_header_text("Found X", "", True) == "Found X [in progress]"
