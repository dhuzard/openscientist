"""
Async discovery loop for OpenScientist autonomous research.

The public entry point is run_discovery_async(), which the JobManager thread
calls via asyncio.run().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from openscientist.agent.base import (
    AbstractAgent,
    AgentConfig,
    IterationResult,
    TokenUsage,
    TurnOutcome,
)
from openscientist.agent.factory import agent_class_for_provider_id, get_agent
from openscientist.database.models import JobDataFile
from openscientist.database.models.job import Job as JobModel
from openscientist.database.session import AsyncSessionLocal
from openscientist.evidence_librarian import initialise_evidence_trace
from openscientist.exceptions import OpenScientistError
from openscientist.job_guidance import (
    has_pending_job_guidance,
    list_pending_job_guidance,
    mark_job_guidance_delivered,
)
from openscientist.knowledge_state import KnowledgeState
from openscientist.orchestrator.iteration import (
    FeedbackWaitResult,
    build_consensus_prompt,
    build_consensus_retry_prompt,
    build_initial_prompt,
    build_iteration_prompt,
    build_report_prompt,
    build_report_retry_prompt,
    increment_ks_iteration,
    update_job_status,
    wait_for_feedback_or_timeout,
)
from openscientist.providers import get_provider
from openscientist.providers.base import Provider
from openscientist.settings import get_settings
from openscientist.transcript import TranscriptEntry, save_transcript
from openscientist.version import get_version_string

logger = logging.getLogger(__name__)


class _DiscoveryCancelledError(RuntimeError):
    """Raised when a job is cancelled during discovery execution."""


class _DiscoveryPausedError(RuntimeError):
    """Raised when a job is paused during discovery execution."""


class _DiscoveryReportRequestedError(RuntimeError):
    """Raised when the user requests an early report."""


@dataclass(frozen=True)
class _ReportOutcome:
    """Outcome of report-generation phase."""

    success: bool
    error: str


def _resolve_primary_data_file(data_files: list[str]) -> Path | None:
    """Resolve the primary data file path for the agent executor."""
    if not data_files:
        return None
    data_file = Path(data_files[0])
    if not data_file.is_absolute():
        return data_file.absolute()
    return data_file


def _build_agent_executor(
    job_dir: Path,
    data_file: Path | None,
    *,
    use_hypotheses: bool = False,
    data_files: list[Path] | None = None,
    assigned_skill_ids: list[str] | None = None,
) -> AbstractAgent[Provider]:
    """Create a configured agent for discovery/report phases.

    The backend that drives the configured provider chooses its own discovery
    system prompt: Claude returns a concise prompt (its rich ``CLAUDE.md`` is
    written separately into ``.claude/`` by ``prepare_job_workspace``), codex
    returns the full per-job doc delivered via ``AGENTS.md``.
    """
    agent_cls = agent_class_for_provider_id(get_settings().provider.provider_id)
    system_prompt = agent_cls.discovery_system_prompt(
        use_hypotheses=use_hypotheses,
        phenix_available=get_settings().phenix.is_available,
    )
    logger.info("Built %s system prompt (%d chars)", agent_cls.backend.value, len(system_prompt))
    config = AgentConfig(
        job_dir=job_dir,
        data_file=data_file,
        system_prompt=system_prompt,
        use_hypotheses=use_hypotheses,
        data_files=tuple(data_files or ()),
        assigned_skill_ids=(tuple(assigned_skill_ids) if assigned_skill_ids is not None else None),
    )
    return get_agent(config)


def _append_iteration_artifacts(
    *,
    provenance_dir: Path,
    log_file: Path,
    iteration: int,
    prompt: str,
    result: IterationResult,
    overwrite_log: bool = False,
) -> None:
    """Persist transcript and log entry for a completed iteration."""
    _save_transcript(provenance_dir / f"iter{iteration}_transcript.json", result.transcript)
    _append_log(
        log_file,
        iteration,
        prompt,
        result.output,
        result.tool_calls,
        write=overwrite_log,
        timed_out=result.outcome is TurnOutcome.TIMED_OUT,
    )


def _check_turn_outcome(result: IterationResult, iteration: int) -> None:
    """Apply the loop's per-turn policy.

    FAILED aborts the run. TIMED_OUT is recorded and the loop advances: the
    turn was cut by a wall-clock timeout, and any work done before the cut is
    already persisted via the tools, so a stalled model is surfaced (in the log
    and the honest outcome) rather than silently passed off as success.
    """
    if result.outcome is TurnOutcome.FAILED:
        logger.error("Iteration %d failed: %s", iteration, result.error)
        raise RuntimeError(f"Iteration {iteration} failed: {result.error}")
    if result.outcome is TurnOutcome.TIMED_OUT:
        logger.warning(
            "Iteration %d timed out (tool_calls=%d); advancing, work before the cut is persisted",
            iteration,
            result.tool_calls,
        )
    else:
        logger.info("Iteration %d completed (tool_calls=%d)", iteration, result.tool_calls)


def _sync_version_metadata_if_available(job_id: str) -> None:
    """Store runtime version metadata in knowledge state when available."""
    version_info = get_version_metadata()
    if not version_info:
        return
    ks = KnowledgeState.load_from_database_sync(job_id)
    ks.set_version_info(version_info)
    ks.save_to_database_sync(job_id)


async def _wait_for_coinvestigate_feedback(
    job_dir: Path,
    investigation_mode: str,
    current_iteration: int,
    max_iterations: int,
) -> FeedbackWaitResult | None:
    """Pause for user feedback between iterations in co-investigation mode."""
    if investigation_mode != "coinvestigate" or current_iteration >= max_iterations:
        return None
    try:
        if await has_pending_job_guidance(job_dir.name):
            logger.info(
                "Skipping feedback wait for job %s: scientist guidance is already queued",
                job_dir.name,
            )
            return {"outcome": "continued", "feedback_text": None}
    except Exception as exc:
        # Guidance is an enhancement to the discovery loop. A transient queue
        # read failure must not replace the established HITL workflow.
        logger.warning("Failed to check queued guidance for job %s: %s", job_dir.name, exc)
    await update_job_status(job_dir, "awaiting_feedback")
    wait_result = await wait_for_feedback_or_timeout(job_dir)
    if wait_result["outcome"] in {"feedback", "timeout", "continued"}:
        await update_job_status(job_dir, "running")
    return wait_result


async def _job_control_checkpoint(job_id: str) -> int:
    """Apply cooperative job controls and return the latest iteration limit."""
    async with AsyncSessionLocal(thread_safe=True) as session:
        result = await session.execute(
            select(JobModel.status, JobModel.max_iterations).where(JobModel.id == UUID(job_id))
        )
        row = result.one_or_none()
    if row is None:
        raise RuntimeError(f"Job {job_id} disappeared during discovery")
    status, max_iterations = row
    if status == "cancelled":
        raise _DiscoveryCancelledError(f"Job {job_id} was cancelled")
    if status == "paused":
        raise _DiscoveryPausedError(f"Job {job_id} was paused")
    if status == "generating_report":
        raise _DiscoveryReportRequestedError(f"Early report requested for job {job_id}")
    return int(max_iterations)


def _feedback_from_wait_result(
    job_id: str,
    wait_result: FeedbackWaitResult | None,
) -> str | None:
    if wait_result is None:
        return None
    if wait_result["outcome"] == "cancelled":
        raise _DiscoveryCancelledError(f"Job {job_id} was cancelled")
    if wait_result["outcome"] == "paused":
        raise _DiscoveryPausedError(f"Job {job_id} was paused")
    if wait_result["outcome"] == "report_requested":
        raise _DiscoveryReportRequestedError(f"Early report requested for job {job_id}")
    return wait_result["feedback_text"] if wait_result["outcome"] == "feedback" else None


async def _run_primary_discovery_loop(
    *,
    executor: AbstractAgent[Provider],
    job_dir: Path,
    runtime: dict[str, Any],
    provenance_dir: Path,
    log_file: Path,
) -> None:
    """Run initial and iterative discovery phases before report generation."""
    job_id = runtime["job_id"]
    max_iterations = int(runtime["max_iterations"])
    data_files = runtime["data_files"]
    investigation_mode = runtime["investigation_mode"]
    requested_resume = runtime.get("resume_iteration")
    start_iteration = max(1, min(int(requested_resume), max_iterations)) if requested_resume else 1
    pending_feedback: str | None = None
    reset_interval = 5

    if start_iteration <= 1:
        current_limit = await _job_control_checkpoint(job_id)
        ks = KnowledgeState.load_from_database_sync(job_id)
        initial_prompt = build_initial_prompt(
            runtime["research_question"],
            current_limit,
            data_files,
            ks,
            description=runtime.get("description"),
        )

        logger.info("Iteration 1/%d: Starting session", current_limit)
        result = await _run_cost_tracked_iteration(
            executor,
            job_id,
            initial_prompt,
            reset_session=True,
            iteration=1,
            operation_type="discovery",
        )
        _check_turn_outcome(result, 1)

        _sync_version_metadata_if_available(job_id)
        _append_iteration_artifacts(
            provenance_dir=provenance_dir,
            log_file=log_file,
            iteration=1,
            prompt=initial_prompt,
            result=result,
            overwrite_log=True,
        )
        current_limit = await _job_control_checkpoint(job_id)
        if current_limit <= 1:
            logger.info("Iteration limit reached after iteration 1")
            return
        increment_ks_iteration(job_id)

        pending_feedback_result = await _wait_for_coinvestigate_feedback(
            job_dir,
            investigation_mode,
            current_iteration=1,
            max_iterations=current_limit,
        )
        pending_feedback = _feedback_from_wait_result(job_id, pending_feedback_result)
        start_iteration = 2
    else:
        logger.info(
            "Resuming discovery at iteration %d/%d with persisted knowledge state",
            start_iteration,
            max_iterations,
        )

    for iteration in range(start_iteration, max_iterations + 1):
        current_limit = await _job_control_checkpoint(job_id)
        if iteration > current_limit:
            logger.info(
                "Reduced iteration limit %d reached before iteration %d",
                current_limit,
                iteration,
            )
            break
        ks = KnowledgeState.load_from_database_sync(job_id)
        if pending_feedback is None:
            pending_feedback = ks.get_feedback_for_iteration(iteration)
        try:
            queued_guidance = await list_pending_job_guidance(job_id)
        except Exception as exc:
            logger.warning("Failed to load queued guidance for job %s: %s", job_id, exc)
            queued_guidance = []

        iteration_prompt = build_iteration_prompt(
            iteration,
            current_limit,
            ks,
            pending_feedback,
            description=runtime.get("description"),
            queued_ideas=[guidance.content for guidance in queued_guidance],
        )
        pending_feedback = None
        should_reset = iteration == start_iteration or iteration % reset_interval == 1
        logger.info(
            "Iteration %d/%d (%s)",
            iteration,
            current_limit,
            "fresh session" if should_reset else "continuing",
        )

        result = await _run_cost_tracked_iteration(
            executor,
            job_id,
            iteration_prompt,
            reset_session=should_reset,
            iteration=iteration,
            operation_type="discovery",
        )
        _check_turn_outcome(result, iteration)
        _append_iteration_artifacts(
            provenance_dir=provenance_dir,
            log_file=log_file,
            iteration=iteration,
            prompt=iteration_prompt,
            result=result,
        )
        if queued_guidance:
            try:
                await mark_job_guidance_delivered(
                    job_id,
                    [guidance.id for guidance in queued_guidance],
                    iteration,
                )
            except Exception as exc:
                # Keep the run moving. Because only the frozen IDs from this
                # turn are marked, a failed acknowledgement leaves them queued
                # for a transparent retry on the next iteration.
                logger.warning(
                    "Failed to mark queued guidance delivered for job %s: %s",
                    job_id,
                    exc,
                )

        current_limit = await _job_control_checkpoint(job_id)
        if iteration >= current_limit:
            logger.info("Reduced iteration limit %d reached", current_limit)
            break
        increment_ks_iteration(job_id)
        pending_feedback_result = await _wait_for_coinvestigate_feedback(
            job_dir,
            investigation_mode,
            current_iteration=iteration,
            max_iterations=current_limit,
        )
        pending_feedback = _feedback_from_wait_result(job_id, pending_feedback_result)

    logger.info("Discovery loop completed")


def _save_report_transcript(job_dir: Path, transcript: list[TranscriptEntry]) -> None:
    """Persist report-generation transcript artifact."""
    provenance_dir = job_dir / "provenance"
    provenance_dir.mkdir(parents=True, exist_ok=True)
    _save_transcript(provenance_dir / "report_transcript.json", transcript)


def _ensure_report_written(
    report_path: Path,
    report_result: IterationResult,
    *,
    baseline_mtime_ns: int | None = None,
) -> bool:
    """Return True only if the report was actually (re)written this turn.

    A weak model sometimes ends the turn claiming "report written" without ever
    calling its file-writing tool. Existence alone is therefore not proof: when
    a stale report from a previous run is already on disk (most importantly
    during report regeneration, but also any re-run), the file "exists" yet was
    never touched, and the turn would be wrongly accepted while the old content
    is served.

    ``baseline_mtime_ns`` is the report's modification time captured *before*
    the turn started (None if it did not exist then). The file counts as
    written only if it now exists and is strictly newer than that baseline.
    If the agent wrote the file to a subdirectory within the job dir, move it
    to the expected path. Returns False when no fresh report can be found, so
    the caller re-asks, then marks the job as failed.
    """

    def _is_fresh(path: Path) -> bool:
        if baseline_mtime_ns is None:
            return True
        try:
            return path.stat().st_mtime_ns > baseline_mtime_ns
        except OSError:
            return False

    if report_path.exists() and _is_fresh(report_path):
        return True

    # Check if the agent nested the file within the job directory. A nested file
    # is one the agent just produced, so it is fresh by construction.
    job_dir = report_path.parent
    for found in job_dir.rglob("final_report.md"):
        if found != report_path and _is_fresh(found):
            logger.warning("Report found at %s, moving to %s", found, report_path)
            found.rename(report_path)
            return True

    logger.error(
        "Report file not freshly written at %s after report iteration "
        "(exists=%s, agent output: %.200s)",
        report_path,
        report_path.exists(),
        report_result.output,
    )
    return False


async def _try_generate_report_pdf(report_path: Path) -> None:
    """Generate HTML and PDF from markdown report.

    Pipeline: markdown (with figure tags) → HTML → PDF (via WeasyPrint).
    Falls back to fpdf2 if WeasyPrint is unavailable or fails.
    """
    job_dir = report_path.parent
    html_path = job_dir / "final_report.html"
    pdf_path = job_dir / "final_report.pdf"

    try:
        from openscientist.report.pdf import render_report_pdf
        from openscientist.report.renderer import render_report_html

        # Render HTML with file:// image paths (for WeasyPrint)
        html_content = render_report_html(report_path, job_dir)
        html_path.write_text(html_content, encoding="utf-8")
        logger.info("HTML report written: %s", html_path)

        # Render PDF from HTML
        await render_report_pdf(html_path, pdf_path, job_dir)
        return

    except Exception as exc:
        logger.warning("WeasyPrint PDF generation failed, falling back to fpdf2: %s", exc)

    # Fallback: strip figure tags and use fpdf2
    try:
        from openscientist.pdf_generator import markdown_to_pdf
        from openscientist.report.processor import strip_figure_tags

        raw_md = report_path.read_text(encoding="utf-8")
        stripped = strip_figure_tags(raw_md)
        # Write stripped version to a temp path for fpdf2
        stripped_path = job_dir / "_final_report_stripped.md"
        stripped_path.write_text(stripped, encoding="utf-8")
        try:
            markdown_to_pdf(stripped_path, pdf_path, add_footer=True)
        finally:
            stripped_path.unlink(missing_ok=True)
    except Exception as fallback_exc:
        logger.warning("fpdf2 fallback also failed: %s", fallback_exc)


# A weak model sometimes ends a turn without actually producing the deliverable
# (e.g. it describes the report instead of writing the file). Re-ask in the same
# session a bounded number of times. The model still authors it, and the job
# fails honestly if the attempts are exhausted.
_MAX_REPORT_ATTEMPTS = 3
_MAX_CONSENSUS_ATTEMPTS = 3


async def _run_report_turn(
    executor: AbstractAgent[Provider],
    job_dir: Path,
    research_question: str,
    ks: KnowledgeState,
    description: str | None,
) -> tuple[IterationResult, bool]:
    """Run the report turn, re-asking until the model creates final_report.md.

    The report turn continues the agent's existing session rather than resetting
    it: a weak model that was reliably calling tools mid-investigation tends to
    drop into chat mode (printing the tool call as text) when handed a large
    "write this" prompt as the first message of a fresh session. Keeping the
    session preserves that tool-using momentum. (A regeneration run, which has
    no prior session, simply starts one here.) Returns the last turn result and
    whether the report file now exists.
    """
    report_path = job_dir / "final_report.md"
    # Snapshot the existing report's mtime (if any) so a stale file from a
    # prior run cannot be mistaken for this turn's output: the model must
    # produce a file strictly newer than this. None means no report yet.
    baseline_mtime_ns = report_path.stat().st_mtime_ns if report_path.exists() else None
    file_write_tool = executor.file_write_tool
    context_window_tokens = executor.model_profile.context_window_tokens
    prompt = build_report_prompt(
        research_question,
        ks,
        job_dir=job_dir,
        description=description,
        file_write_tool=file_write_tool,
        context_window_tokens=context_window_tokens,
    )
    logger.info("Report generation turn (prompt: %d chars)", len(prompt))

    result = await _run_cost_tracked_iteration(
        executor,
        job_dir.name,
        prompt,
        reset_session=False,
        operation_type="report",
    )
    for attempt in range(1, _MAX_REPORT_ATTEMPTS + 1):
        if _ensure_report_written(report_path, result, baseline_mtime_ns=baseline_mtime_ns):
            if attempt > 1:
                logger.info("Report written on attempt %d", attempt)
            return result, True
        if attempt == _MAX_REPORT_ATTEMPTS:
            break
        logger.warning(
            "Report file missing after attempt %d/%d; re-asking", attempt, _MAX_REPORT_ATTEMPTS
        )
        result = await _run_cost_tracked_iteration(
            executor,
            job_dir.name,
            build_report_retry_prompt(
                research_question,
                ks,
                job_dir=job_dir,
                description=description,
                file_write_tool=file_write_tool,
                context_window_tokens=context_window_tokens,
            ),
            reset_session=False,
            operation_type="report",
        )
    logger.error("Report file not written after %d attempts", _MAX_REPORT_ATTEMPTS)
    return result, False


async def _set_consensus_answer(
    executor: AbstractAgent[Provider], job_dir: Path, research_question: str
) -> None:
    """Run the consensus turn, re-asking until the model records a fresh answer.

    The model writes the consensus itself. This only re-prompts. A freshness
    guard mirrors the report file's: snapshot the prior ``consensus_answer``
    (None on a fresh run, the previous run's answer on regeneration) and accept
    only a value the model wrote *this* turn, so a regenerated report cannot
    ship the stale consensus. If the attempts are exhausted the report still
    stands and the job completes with the prior consensus (logged), rather than
    fabricating one.
    """
    baseline = KnowledgeState.load_from_database_sync(job_dir.name).data.get("consensus_answer")
    for attempt in range(1, _MAX_CONSENSUS_ATTEMPTS + 1):
        prompt = (
            build_consensus_prompt(research_question)
            if attempt == 1
            else build_consensus_retry_prompt(research_question)
        )
        await _run_cost_tracked_iteration(
            executor,
            job_dir.name,
            prompt,
            reset_session=False,
            operation_type="consensus",
        )
        current = KnowledgeState.load_from_database_sync(job_dir.name).data.get("consensus_answer")
        if current and current != baseline:
            if attempt > 1:
                logger.info("Consensus recorded on attempt %d", attempt)
            return
        if attempt < _MAX_CONSENSUS_ATTEMPTS:
            logger.warning(
                "Consensus not recorded after attempt %d/%d; re-asking",
                attempt,
                _MAX_CONSENSUS_ATTEMPTS,
            )
    logger.warning("Consensus answer not recorded after %d attempts", _MAX_CONSENSUS_ATTEMPTS)


async def _run_report_generation_phase(
    executor: AbstractAgent[Provider],
    job_dir: Path,
    research_question: str,
    description: str | None = None,
) -> _ReportOutcome:
    """Run the report and consensus turns (each with bounded retries) and output
    artifact handling."""
    ks = KnowledgeState.load_from_database_sync(job_dir.name)
    report_result, report_success = await _run_report_turn(
        executor, job_dir, research_question, ks, description
    )
    _save_report_transcript(job_dir, report_result.transcript)
    report_path = job_dir / "final_report.md"

    if report_success:
        # Dedicated consensus turn (separate from the report so a weaker model
        # commits fully to one deliverable at a time). The model writes it.
        await _set_consensus_answer(executor, job_dir, research_question)

        try:
            await _try_generate_report_pdf(report_path)
        except (ValueError, OSError, OpenScientistError) as exc:
            logger.warning("PDF generation failed: %s", exc)

    return _ReportOutcome(success=report_success, error=report_result.error)


async def _persist_final_status(
    job_dir: Path,
    report_outcome: _ReportOutcome,
) -> str:
    """Persist final job status based on report generation outcome."""
    final_status = "completed" if report_outcome.success else "failed"
    if final_status == "completed":
        await update_job_status(job_dir, "completed")
    else:
        await update_job_status(
            job_dir,
            "failed",
            error_message=f"Report generation failed: {report_outcome.error}",
        )
    return final_status


async def _load_runtime_context(job_dir: Path) -> dict[str, Any]:
    """Load runtime job metadata from the database."""
    job_uuid = UUID(job_dir.name)

    async with AsyncSessionLocal(thread_safe=True) as session:
        job_result = await session.execute(select(JobModel).where(JobModel.id == job_uuid))
        job = job_result.scalar_one_or_none()
        if job is None:
            raise ValueError(f"Job {job_uuid} not found in database")

        files_result = await session.execute(
            select(JobDataFile.file_path)
            .where(JobDataFile.job_id == job_uuid)
            .order_by(JobDataFile.created_at.asc())
        )
        data_files = [str(path) for path in files_result.scalars().all()]

    resolved_files: list[str] = []
    for raw_path in data_files:
        file_path = Path(raw_path)
        if not file_path.is_absolute():
            file_path = job_dir / file_path
        resolved_files.append(str(file_path))

    return {
        "job_id": str(job.id),
        "research_question": job.research_question,
        "description": getattr(job, "description", None),
        "max_iterations": job.max_iterations,
        "resume_iteration": max(
            int(job.resume_iteration or 1),
            int(job.current_iteration or 1),
        ),
        "use_hypotheses": bool(job.use_hypotheses),
        "assigned_skill_ids": getattr(job, "assigned_skill_ids", None),
        "investigation_mode": job.investigation_mode,
        "data_files": resolved_files,
    }


def get_version_metadata() -> dict[str, str]:
    """Get OpenScientist version metadata for reproducibility."""
    import os

    from openscientist.version import SHORT_COMMIT_LENGTH, get_commit

    metadata: dict[str, str] = {}

    commit = get_commit()
    if commit != "unknown":
        metadata["openscientist_commit"] = commit

    openscientist_build_time = os.environ.get("OPENSCIENTIST_BUILD_TIME")  # env-ok
    if openscientist_build_time and openscientist_build_time != "unknown":
        metadata["openscientist_build_time"] = openscientist_build_time

    try:
        if Path("/.dockerenv").exists():
            with open("/etc/hostname", encoding="utf-8") as f:
                container_id = f.read().strip()
                if container_id:
                    metadata["docker_container_id"] = container_id[:SHORT_COMMIT_LENGTH]
    except OSError:
        pass

    return metadata


async def _persist_job_cost_record(
    job_id: str,
    tokens: TokenUsage,
    provider_name: str,
    model_name: str,
    operation_type: str = "discovery",
    iteration: int | None = None,
) -> None:
    """Write a CostRecord for one completed agent turn."""
    from openscientist.database.models import CostRecord
    from openscientist.providers.pricing import estimate_cost_usd

    cost_usd = estimate_cost_usd(
        model_name,
        tokens.input_tokens,
        tokens.output_tokens,
        cache_write_tokens=tokens.cache_write_tokens,
        cache_read_tokens=tokens.cache_read_tokens,
        reasoning_tokens=tokens.reasoning_tokens,
    )
    async with AsyncSessionLocal(thread_safe=True) as session:
        record = CostRecord(
            job_id=UUID(job_id),
            iteration=iteration,
            operation_type=operation_type,
            provider=provider_name,
            model=model_name,
            input_tokens=tokens.input_tokens,
            output_tokens=tokens.output_tokens,
            cache_write_tokens=tokens.cache_write_tokens,
            cache_read_tokens=tokens.cache_read_tokens,
            reasoning_tokens=tokens.reasoning_tokens,
            cost_usd=cost_usd,
        )
        session.add(record)
        await session.commit()


async def _run_cost_tracked_iteration(
    executor: AbstractAgent[Provider],
    job_id: str,
    prompt: str,
    *,
    reset_session: bool,
    operation_type: str,
    iteration: int | None = None,
) -> IterationResult:
    """Run one model turn and immediately record its incremental token cost."""
    current_usage = getattr(executor, "total_tokens", None)
    before = TokenUsage(**vars(current_usage)) if isinstance(current_usage, TokenUsage) else None
    result = await executor.run_iteration(prompt, reset_session=reset_session)
    after = getattr(executor, "total_tokens", None)
    if before is None or not isinstance(after, TokenUsage):
        return result
    delta = TokenUsage(
        input_tokens=max(0, after.input_tokens - before.input_tokens),
        output_tokens=max(0, after.output_tokens - before.output_tokens),
        cache_write_tokens=max(0, after.cache_write_tokens - before.cache_write_tokens),
        cache_read_tokens=max(0, after.cache_read_tokens - before.cache_read_tokens),
        reasoning_tokens=max(0, after.reasoning_tokens - before.reasoning_tokens),
    )
    if not any(vars(delta).values()):
        return result

    try:
        provider = getattr(executor, "provider", None) or get_provider()
        model_name = (
            getattr(executor, "effective_model_name", None)
            or provider.effective_model_name()
            or "unknown"
        )
        await _persist_job_cost_record(
            job_id,
            delta,
            provider.display_name,
            model_name,
            operation_type,
            iteration,
        )
    except Exception as cost_err:
        logger.warning("Failed to persist live cost record for job %s: %s", job_id, cost_err)
    return result


async def _finalize_executor(executor: AbstractAgent[Provider], job_id: str) -> None:
    """Log cumulative token usage and shut the executor down.

    Shared ``finally`` handling for both the full discovery run and the
    report-only regeneration run. Individual turns persist their incremental
    cost as soon as they finish, so finalization must not record them again.
    """
    tokens = executor.total_tokens
    logger.info(
        "Agent executor completed: %d input tokens, %d output tokens",
        tokens.input_tokens,
        tokens.output_tokens,
    )
    await executor.shutdown()


async def _build_and_prepare_executor(
    job_dir: Path,
    runtime: dict[str, Any],
    *,
    operation_status: str = "running",
) -> AbstractAgent[Provider]:
    """Build the agent executor and run backend setup.

    Shared by the full discovery run and the report-only regeneration path:
    both need a configured executor whose runtime env is applied and whose
    per-job workspace is materialised before any turn runs. Backend-specific
    setup is the agent's own concern: apply any runtime env (Claude
    auth/routing flags; no-op for codex) and materialise the per-job workspace
    (enabled skills in the backend's on-disk layout).
    """
    use_hypotheses = runtime["use_hypotheses"]
    all_data_files = [Path(p) for p in runtime["data_files"]]
    executor = _build_agent_executor(
        job_dir=job_dir,
        data_file=_resolve_primary_data_file(runtime["data_files"]),
        use_hypotheses=use_hypotheses,
        data_files=all_data_files,
        assigned_skill_ids=runtime.get("assigned_skill_ids"),
    )
    executor.apply_runtime_environment()
    await update_job_status(job_dir, operation_status)
    await executor.prepare_job_workspace(use_hypotheses=use_hypotheses)
    initialise_evidence_trace(job_dir)
    # Resolve the model's context window once per job, off the event loop (the
    # Ollama probe is blocking I/O). Cached on the agent for the report budget.
    await executor.warm_model_profile()
    try:
        from openscientist.agent_task_provenance import write_job_model_runtime

        write_job_model_runtime(
            job_dir,
            provider=executor.provider.display_name,
            model=executor.effective_model_name or "unknown",
            backend=executor.backend.value,
            context_window_tokens=executor.model_profile.context_window_tokens,
        )
    except Exception as exc:
        logger.warning("Failed to persist runtime model identity for %s: %s", job_dir.name, exc)
    return executor


async def regenerate_report_async(job_dir: Path) -> dict[str, Any]:
    """Re-run only the report-generation phase for an already-finished job.

    Backs the admin "Regenerate report" action. The discovery iterations are
    NOT re-run: every finding already lives in the persisted ``KnowledgeState``
    and the report turn starts a fresh agent session, so this needs only the
    configured executor and the runtime context. It overwrites
    ``final_report.md`` (and its PDF) and persists the final job status, exactly
    like the report tail of ``run_discovery_async``.
    """
    job_dir = Path(job_dir)
    runtime = await _load_runtime_context(job_dir)
    job_id = runtime["job_id"]
    logger.info("Regenerating report for job %s", job_id)

    executor = await _build_and_prepare_executor(
        job_dir,
        runtime,
        operation_status="generating_report",
    )
    try:
        report_outcome = await _run_report_generation_phase(
            executor=executor,
            job_dir=job_dir,
            research_question=runtime["research_question"],
            description=runtime.get("description"),
        )
        final_status = await _persist_final_status(job_dir, report_outcome)
        ks = KnowledgeState.load_from_database_sync(job_id)
        return {
            "job_id": job_id,
            "status": final_status,
            "iterations": ks.data["iteration"],
            "findings": len(ks.data["findings"]),
        }
    except Exception as e:
        logger.error("Report regeneration failed [%s]: %s", get_version_string(), e, exc_info=True)
        try:
            await update_job_status(job_dir, "failed", error_message=str(e))
        except Exception as status_error:
            logger.warning("Failed to persist failure status for job %s: %s", job_id, status_error)
        try:
            ks = KnowledgeState.load_from_database_sync(job_id)
            iterations = ks.data["iteration"]
            findings = len(ks.data["findings"])
        except Exception:
            iterations = 0
            findings = 0
        return {
            "job_id": job_id,
            "status": "failed",
            "iterations": iterations,
            "findings": findings,
        }
    finally:
        await _finalize_executor(executor, job_id)


async def run_discovery_async(job_dir: Path) -> dict[str, Any]:
    """
    Run autonomous discovery using the configured agent executor.

    This is an async entry point that JobManager (or the container entrypoint)
    calls.  The agent is chosen by agent.factory.get_agent() based
    on the configured provider.

    Args:
        job_dir: Path to job directory

    Returns:
        Dict: {job_id, status, iterations, findings}
    """
    job_dir = Path(job_dir)
    runtime = await _load_runtime_context(job_dir)
    job_id = runtime["job_id"]
    logger.info("Starting discovery for job %s (mode=%s)", job_id, runtime["investigation_mode"])

    executor = await _build_and_prepare_executor(job_dir, runtime)
    logger.info("Created agent executor for job %s", job_id)

    provenance_dir = job_dir / "provenance"
    provenance_dir.mkdir(parents=True, exist_ok=True)
    log_file = job_dir / "claude_iterations.log"

    try:
        await _run_primary_discovery_loop(
            executor=executor,
            job_dir=job_dir,
            runtime=runtime,
            provenance_dir=provenance_dir,
            log_file=log_file,
        )
        report_outcome = await _run_report_generation_phase(
            executor=executor,
            job_dir=job_dir,
            research_question=runtime["research_question"],
            description=runtime.get("description"),
        )
        final_status = await _persist_final_status(job_dir, report_outcome)
        ks = KnowledgeState.load_from_database_sync(job_id)
        return {
            "job_id": job_id,
            "status": final_status,
            "iterations": ks.data["iteration"],
            "findings": len(ks.data["findings"]),
        }

    except _DiscoveryPausedError:
        logger.info("Discovery paused for job %s", job_id)
        ks = KnowledgeState.load_from_database_sync(job_id)
        return {
            "job_id": job_id,
            "status": "paused",
            "iterations": ks.data["iteration"],
            "findings": len(ks.data["findings"]),
        }

    except _DiscoveryReportRequestedError:
        logger.info("Early report requested for job %s", job_id)
        report_outcome = await _run_report_generation_phase(
            executor=executor,
            job_dir=job_dir,
            research_question=runtime["research_question"],
            description=runtime.get("description"),
        )
        final_status = await _persist_final_status(job_dir, report_outcome)
        ks = KnowledgeState.load_from_database_sync(job_id)
        return {
            "job_id": job_id,
            "status": final_status,
            "iterations": ks.data["iteration"],
            "findings": len(ks.data["findings"]),
        }

    except _DiscoveryCancelledError:
        logger.info("Discovery cancelled for job %s", job_id)
        ks = KnowledgeState.load_from_database_sync(job_id)
        return {
            "job_id": job_id,
            "status": "cancelled",
            "iterations": ks.data["iteration"],
            "findings": len(ks.data["findings"]),
        }

    except Exception as e:
        logger.error("Discovery failed [%s]: %s", get_version_string(), e, exc_info=True)
        try:
            await update_job_status(job_dir, "failed", error_message=str(e))
        except Exception as status_error:
            logger.warning("Failed to persist failure status for job %s: %s", job_id, status_error)
        try:
            ks = KnowledgeState.load_from_database_sync(job_id)
            iterations = ks.data["iteration"]
            findings = len(ks.data["findings"])
        except Exception:
            iterations = 0
            findings = 0
        return {
            "job_id": job_id,
            "status": "failed",
            "iterations": iterations,
            "findings": findings,
            "error": str(e),
        }

    finally:
        await _finalize_executor(executor, job_id)


def _save_transcript(path: Path, transcript: list[TranscriptEntry]) -> None:
    """Save iteration transcript to JSON file."""
    save_transcript(path, transcript)
    try:
        from openscientist.skill_provenance import write_job_skill_provenance

        write_job_skill_provenance(path.parent.parent)
    except Exception as exc:
        logger.warning("Failed to update skill provenance for %s: %s", path, exc)
    logger.info("Saved transcript to %s", path)


def _append_log(
    log_file: Path,
    iteration: int,
    prompt: str,
    output: str,
    tool_calls: int,
    write: bool = False,
    timed_out: bool = False,
) -> None:
    """Append iteration summary to the log file."""
    mode = "w" if write else "a"
    with open(log_file, mode, encoding="utf-8") as f:
        f.write(f"=== Iteration {iteration} ===\n")
        f.write(f"Prompt: {prompt}\n\n")
        f.write(f"Output: {output}\n\n")
        f.write(f"Tool calls: {tool_calls}\n\n")
        if timed_out:
            f.write("Timed out: yes (turn cut by the wall-clock limit)\n\n")
