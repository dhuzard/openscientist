"""Tests for job-detail action gating (admin "Regenerate report")."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openscientist.job.types import JobStatus
from openscientist.webapp_components.pages import job_detail


def _context(status: JobStatus) -> SimpleNamespace:
    """Minimal stand-in carrying only what _can_regenerate_report reads."""
    return SimpleNamespace(job_info=SimpleNamespace(status=status))


def _action_context() -> SimpleNamespace:
    """Stand-in for the regenerate action handler with a recording manager."""
    return SimpleNamespace(
        job_id="job-1",
        job_manager=SimpleNamespace(regenerate_report=MagicMock()),
    )


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


class TestDownloadArtifactsZipOffloadsToWorkerThread:
    """Building the artifacts ZIP reads/compresses the whole job dir, which
    can take a while for data-heavy jobs. It must run via nicegui.run.io_bound
    (a worker thread) rather than directly on the shared event loop, or one
    slow download blocks the entire app for every connected user."""

    @pytest.mark.asyncio
    async def test_zip_creation_runs_via_io_bound(self, tmp_path) -> None:
        fake_buffer = MagicMock()
        fake_buffer.getvalue.return_value = b"zip-bytes"

        async def fake_io_bound(func, *args):
            assert func is job_detail.create_artifacts_zip
            assert args == (tmp_path, "job-1")
            return fake_buffer

        with (
            patch.object(job_detail.run, "io_bound", side_effect=fake_io_bound) as mock_io_bound,
            patch.object(job_detail, "ui") as mock_ui,
        ):
            await job_detail._download_artifacts_zip(tmp_path, "job-1")

        mock_io_bound.assert_called_once()
        mock_ui.download.assert_called_once_with(b"zip-bytes", filename="job-1_artifacts.zip")

    @pytest.mark.asyncio
    async def test_zip_creation_failure_notifies_without_crashing(self, tmp_path) -> None:
        async def failing_io_bound(func, *args):
            raise OSError("disk full")

        with (
            patch.object(job_detail.run, "io_bound", side_effect=failing_io_bound),
            patch.object(job_detail, "ui") as mock_ui,
        ):
            await job_detail._download_artifacts_zip(tmp_path, "job-1")

        mock_ui.notify.assert_called_once()
        mock_ui.download.assert_not_called()
