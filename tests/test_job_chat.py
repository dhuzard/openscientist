"""
Tests for job chat functionality.

Tests chat message creation, conversation history, context loading,
and executor error handling.
"""

import json
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openscientist.agent.base import AbstractAgent, AgentConfig, IterationResult, TurnOutcome
from openscientist.database.models import Job, JobChatMessage, User
from openscientist.database.rls import set_current_user
from openscientist.job_chat import (
    _CHAT_REQUEST_FILE,
    _CHAT_RESPONSE_FILE,
    _assigned_skills_prompt,
    _build_chat_request,
    _load_assigned_skills,
    extract_chat_artifact_images,
    get_chat_history,
    load_job_context,
    normalize_chat_artifact_links,
    run_chat_turn_async,
    send_chat_message,
)
from openscientist.providers.base import Provider
from tests.helpers import StubClaudeProvider as _ChatProvider
from tests.helpers import enable_rls


def _build_agent_recorder(
    captured: dict[str, AgentConfig], result: IterationResult
) -> Callable[[AgentConfig, Provider], AbstractAgent[Provider]]:
    """A `factory.build_agent` replacement for chat tests.

    Builds the real agent (so the backend's chat prep -- chat_system_prompt /
    write_chat_context -- runs for real) but mocks `run_iteration`, and records
    the `AgentConfig` the executor was built with.
    """
    from openscientist.agent import factory

    real_build_agent = factory.build_agent

    def _build(config: AgentConfig, provider: Provider) -> AbstractAgent[Provider]:
        captured["config"] = config
        agent = real_build_agent(config, provider)
        agent.run_iteration = AsyncMock(return_value=result)  # type: ignore[method-assign]
        return agent

    return _build


def test_chat_recovers_assigned_skills_for_follow_up(tmp_path: Path) -> None:
    manifest = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "key": "domain--digital-ventilated-cage-analysis",
            "name": "Digital Ventilated Cage Analysis",
        },
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "key": "domain--data-science",
            "name": "data-science",
        },
    ]
    (tmp_path / ".openscientist_skill_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    skills = _load_assigned_skills(tmp_path)
    prompt = _assigned_skills_prompt(skills)

    assert [skill.skill_id for skill in skills] == [item["id"] for item in manifest]
    assert "domain--digital-ventilated-cage-analysis" in prompt
    assert "domain--data-science" in prompt
    assert "companion-skill rules" in prompt


@contextmanager
def _chat_runner_writing(response):
    """Run the chat turn against a fake container that writes the given response
    file, with a stubbed provider for building the request."""

    def _run(job_id, job_dir, *, timeout=300):
        (Path(job_dir) / _CHAT_RESPONSE_FILE).write_text(json.dumps(response))

    runner = MagicMock()
    runner.run_chat_turn.side_effect = _run
    with (
        patch("openscientist.job_container.runner.JobContainerRunner", return_value=runner),
        patch("openscientist.providers.get_provider", return_value=_ChatProvider()),
    ):
        yield


@contextmanager
def _chat_runner_failing(exc):
    """Chat turn against a fake container whose run_chat_turn raises."""
    runner = MagicMock()
    runner.run_chat_turn.side_effect = exc
    with (
        patch("openscientist.job_container.runner.JobContainerRunner", return_value=runner),
        patch("openscientist.providers.get_provider", return_value=_ChatProvider()),
    ):
        yield


@pytest.mark.asyncio
async def test_create_chat_message(
    db_session: AsyncSession,
    test_user: User,
    test_job: Job,
):
    """Test creating a chat message."""
    _ = test_user
    message = JobChatMessage(
        job_id=test_job.id,
        role="user",
        content="What are the main findings?",
    )
    db_session.add(message)
    await db_session.commit()
    await db_session.refresh(message)

    assert isinstance(message.id, UUID)
    assert message.job_id == test_job.id
    assert message.role == "user"
    assert message.content == "What are the main findings?"
    assert isinstance(message.created_at, datetime)


@pytest.mark.asyncio
async def test_chat_conversation_flow(
    db_session: AsyncSession,
    test_user: User,
    test_job: Job,
):
    """Test a full conversation flow with user and assistant messages."""
    _ = test_user
    # User asks a question
    user_msg = JobChatMessage(
        job_id=test_job.id,
        role="user",
        content="Can you explain the first hypothesis?",
    )
    db_session.add(user_msg)
    await db_session.commit()

    # Assistant responds
    assistant_msg = JobChatMessage(
        job_id=test_job.id,
        role="assistant",
        content="The first hypothesis suggests...",
    )
    db_session.add(assistant_msg)
    await db_session.commit()

    # Query conversation
    stmt = (
        select(JobChatMessage)
        .where(JobChatMessage.job_id == test_job.id)
        .order_by(JobChatMessage.created_at)
    )
    result = await db_session.execute(stmt)
    messages = result.scalars().all()

    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"


@pytest.mark.asyncio
async def test_get_chat_history(
    db_session: AsyncSession,
    test_user: User,
    test_job: Job,
):
    """Test retrieving chat history."""
    _ = test_user
    # Create multiple messages
    messages = [
        JobChatMessage(
            job_id=test_job.id,
            role="user",
            content=f"Question {i}",
        )
        for i in range(5)
    ]

    for msg in messages:
        db_session.add(msg)
    await db_session.commit()

    # Retrieve history
    history = await get_chat_history(db_session, test_job.id, limit=10)

    assert len(history) == 5
    assert all(msg.job_id == test_job.id for msg in history)

    # Should be chronologically ordered
    for i in range(len(history) - 1):
        assert history[i].created_at <= history[i + 1].created_at


@pytest.mark.asyncio
async def test_chat_history_limit(
    db_session: AsyncSession,
    test_user: User,
    test_job: Job,
):
    """Test that chat history respects limit parameter."""
    _ = test_user
    # Create 20 messages
    for i in range(20):
        msg = JobChatMessage(
            job_id=test_job.id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"Message {i}",
        )
        db_session.add(msg)
    await db_session.commit()

    # Retrieve with limit
    history = await get_chat_history(db_session, test_job.id, limit=10)

    assert len(history) == 10


@pytest.mark.asyncio
async def test_chat_messages_per_job(
    db_session: AsyncSession,
    test_user: User,
    test_job: Job,
):
    """Test that chat messages are isolated per job."""
    # Create second job
    job2 = Job(
        owner_id=test_user.id,
        research_question="Second Job",
        description="Another job",
        status="running",
    )
    db_session.add(job2)
    await db_session.commit()
    await db_session.refresh(job2)

    # Add messages to each job
    msg1 = JobChatMessage(
        job_id=test_job.id,
        role="user",
        content="Message for job 1",
    )
    msg2 = JobChatMessage(
        job_id=job2.id,
        role="user",
        content="Message for job 2",
    )

    db_session.add_all([msg1, msg2])
    await db_session.commit()

    # Verify isolation
    history1 = await get_chat_history(db_session, test_job.id)
    history2 = await get_chat_history(db_session, job2.id)

    assert len(history1) == 1
    assert len(history2) == 1
    assert history1[0].content == "Message for job 1"
    assert history2[0].content == "Message for job 2"


@pytest.mark.asyncio
async def test_cascade_delete_chat_messages(
    db_session: AsyncSession,
    test_user: User,
):
    """Test that deleting a job deletes its chat messages."""
    # Create job with messages
    job = Job(
        owner_id=test_user.id,
        research_question="Job with Chat",
        description="Will be deleted",
        status="completed",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # Add chat messages
    messages = [
        JobChatMessage(
            job_id=job.id,
            role="user",
            content=f"Message {i}",
        )
        for i in range(3)
    ]

    for msg in messages:
        db_session.add(msg)
    await db_session.commit()

    job_id = job.id

    # Delete job
    await db_session.delete(job)
    await db_session.commit()

    # Verify messages are deleted
    stmt = select(JobChatMessage).where(JobChatMessage.job_id == job_id)
    result = await db_session.execute(stmt)
    remaining_messages = result.scalars().all()

    assert len(remaining_messages) == 0


@pytest.mark.asyncio
async def test_load_job_context_empty_dir():
    """Test loading context when no knowledge state exists in the DB."""
    job_id = "not-a-valid-uuid"

    context = await load_job_context(job_id)

    # Should return empty or minimal context
    assert isinstance(context, str)
    # Missing DB record means no context loaded, but function shouldn't crash


@pytest.mark.asyncio
async def test_load_job_context_with_knowledge_state_config():
    """Test loading context with research question in knowledge state."""
    from openscientist.knowledge_state import KnowledgeState

    job_id = "test_job_456"

    ks = KnowledgeState(job_id, "What is the crystal structure?", 10)

    with patch(
        "openscientist.job_chat.KnowledgeState.load_from_database_sync",
        return_value=ks,
    ):
        context = await load_job_context(job_id)

    assert "What is the crystal structure?" in context
    assert "Research Question" in context


@pytest.mark.asyncio
async def test_load_job_context_with_knowledge_state():
    """Test loading context with knowledge state including findings."""
    from openscientist.knowledge_state import KnowledgeState

    job_id = "test_job_789"

    ks = KnowledgeState(job_id, "Protein analysis?", 10)
    ks.data["findings"] = [
        {
            "content": "The protein shows high binding affinity",
            "importance": "high",
            "confidence": "strong",
            "evidence": ["Data point 1", "Data point 2"],
        },
        {
            "content": "Secondary structure is alpha-helical",
            "importance": "medium",
            "confidence": "moderate",
            "evidence": ["Observation 1"],
        },
    ]
    ks.data["hypotheses"] = [
        {
            "hypothesis": "The binding site is at position X",
            "status": "active",
            "rationale": "Based on structural analysis",
        },
    ]
    ks.data["literature"] = [
        {
            "title": "Crystal Structures of Proteins",
            "relevance_score": 0.92,
            "key_findings": ["Finding 1", "Finding 2"],
        },
    ]
    ks.data["iteration_summaries"] = [
        {
            "iteration": 1,
            "strapline": "Initial analysis",
            "summary": "Started with basic structure analysis",
        },
    ]

    with patch(
        "openscientist.job_chat.KnowledgeState.load_from_database_sync",
        return_value=ks,
    ):
        context = await load_job_context(job_id)

    # Check findings are included
    assert "The protein shows high binding affinity" in context
    assert "high" in context
    assert "Evidence:" in context

    # Check hypotheses
    assert "The binding site is at position X" in context
    assert "active" in context

    # Check literature
    assert "Crystal Structures of Proteins" in context

    # Check iteration summaries
    assert "Initial analysis" in context


@pytest.mark.asyncio
async def test_load_job_context_supports_modern_knowledge_state_keys():
    """Context rendering should support modern finding/hypothesis key names."""
    from openscientist.knowledge_state import KnowledgeState

    job_id = "test_job_modern_keys"

    ks = KnowledgeState(job_id, "Q?", 10)
    ks.data["findings"] = [
        {
            "title": "Modern finding title",
            "importance": "high",
            "confidence": "strong",
        }
    ]
    ks.data["hypotheses"] = [
        {
            "statement": "Modern hypothesis statement",
            "status": "supported",
        }
    ]

    with patch(
        "openscientist.job_chat.KnowledgeState.load_from_database_sync",
        return_value=ks,
    ):
        context = await load_job_context(job_id)
    assert "Modern finding title" in context
    assert "Modern hypothesis statement" in context


@pytest.mark.asyncio
async def test_chat_message_role_validation(
    db_session: AsyncSession,
    test_user: User,
    test_job: Job,
):
    """Test that chat messages have proper role values."""
    _ = test_user
    # Create user message
    user_msg = JobChatMessage(
        job_id=test_job.id,
        role="user",
        content="User question",
    )
    db_session.add(user_msg)
    await db_session.commit()

    # Create assistant message
    assistant_msg = JobChatMessage(
        job_id=test_job.id,
        role="assistant",
        content="Assistant response",
    )
    db_session.add(assistant_msg)
    await db_session.commit()

    # Query and verify
    stmt = (
        select(JobChatMessage)
        .where(JobChatMessage.job_id == test_job.id)
        .order_by(JobChatMessage.created_at)
    )
    result = await db_session.execute(stmt)
    messages = result.scalars().all()

    assert messages[0].role == "user"
    assert messages[1].role == "assistant"


@pytest.mark.asyncio
async def test_chat_access_with_rls(
    db_session: AsyncSession,
    test_user: User,
    test_user2: User,
):
    """Test that chat messages respect RLS policies."""
    # Create job for test_user
    job = Job(
        owner_id=test_user.id,
        research_question="Private Job",
        description="Test RLS",
        status="running",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # Add chat message
    message = JobChatMessage(
        job_id=job.id,
        role="user",
        content="Private message",
    )
    db_session.add(message)
    await db_session.commit()

    # Enable RLS before setting user context (superuser bypasses RLS)
    await enable_rls(db_session)

    # Try to access as test_user2 (should fail with RLS)
    await set_current_user(db_session, test_user2.id)

    # Since job is not visible, chat messages won't be either
    stmt = select(JobChatMessage).where(JobChatMessage.job_id == job.id)
    result = await db_session.execute(stmt)
    messages = result.scalars().all()

    # RLS should prevent access (assuming chat messages inherit job access)
    # Actual behavior depends on RLS policy implementation
    assert len(messages) == 0


@pytest.mark.asyncio
async def test_send_chat_message_success(
    db_session: AsyncSession,
    test_user: User,
    test_job: Job,
    temp_jobs_dir: Path,
):
    """send_chat_message runs the turn in a container and stores both messages."""
    _ = test_user
    job_dir = temp_jobs_dir / str(test_job.id)
    job_dir.mkdir()

    with _chat_runner_writing({"output": "The main findings indicate..."}):
        response = await send_chat_message(
            db_session, test_job.id, "What are the main findings?", job_dir
        )

    assert response == "The main findings indicate..."
    history = await get_chat_history(db_session, test_job.id)
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].content == "What are the main findings?"
    assert history[1].role == "assistant"
    assert history[1].content == "The main findings indicate..."


@pytest.mark.asyncio
async def test_new_chat_plot_is_embedded_and_creates_report_revision(
    db_session: AsyncSession,
    test_user: User,
    test_job: Job,
    temp_jobs_dir: Path,
):
    """A generated plot is previewed, inserted, and versioned by the host."""
    _ = test_user
    job_dir = temp_jobs_dir / str(test_job.id)
    (job_dir / "plots").mkdir(parents=True)
    (job_dir / "provenance").mkdir()
    (job_dir / "final_report.md").write_text("# Original report\n", encoding="utf-8")
    (job_dir / "final_report.html").write_text("<p>original</p>", encoding="utf-8")
    (job_dir / "final_report.pdf").write_bytes(b"original pdf")

    async def _generate_plot(*_args: object, **_kwargs: object) -> str:
        (job_dir / "plots" / "mean_circadian.png").write_bytes(b"plot bytes")
        (job_dir / "provenance" / "plot_5.png").write_bytes(b"plot bytes")
        (job_dir / "provenance" / "plot_5.json").write_text(
            json.dumps(
                {
                    "also_saved_as": "plots/mean_circadian.png",
                    "description": (
                        "Mean circadian activity centered on the assumed 18:00 UTC dark onset."
                    ),
                }
            ),
            encoding="utf-8",
        )
        return "Added the mean circadian plot.\n\nFiles created: plots/mean_circadian.png"

    async def _render_outputs(path: Path) -> None:
        markdown = (path / "final_report.md").read_text(encoding="utf-8")
        (path / "final_report.html").write_text(markdown, encoding="utf-8")
        (path / "final_report.pdf").write_bytes(b"updated pdf")

    with (
        patch(
            "openscientist.job_chat._send_message_via_container",
            side_effect=_generate_plot,
        ),
        patch(
            "openscientist.job_chat._render_report_outputs",
            side_effect=_render_outputs,
        ),
    ):
        response = await send_chat_message(
            db_session,
            test_job.id,
            "Add a mean circadian plot.",
            job_dir,
        )

    assert f"![Mean Circadian](/jobs/{test_job.id}/plots/mean_circadian.png)" in response
    assert "Scientific Report updated — version v2" in response
    assert "Scientific Report → Follow-up analyses from Chat" in response
    assert "assumed 18:00 UTC dark onset" in response

    report = (job_dir / "final_report.md").read_text(encoding="utf-8")
    assert "## Follow-up analyses from Chat" in report
    assert "{{figure:plots/mean_circadian.png" in report
    assert "assumed 18:00 UTC dark onset" in report

    manifest = json.loads(
        (job_dir / "provenance" / "report_versions.json").read_text(encoding="utf-8")
    )
    assert manifest["current_version"] == 2
    assert [item["version"] for item in manifest["versions"]] == [1, 2]
    assert manifest["versions"][1]["section"] == "Follow-up analyses from Chat"
    assert (job_dir / "report_versions" / "v1" / "final_report.md").read_text(
        encoding="utf-8"
    ) == "# Original report\n"
    assert "Follow-up analyses from Chat" in (
        job_dir / "report_versions" / "v2" / "final_report.md"
    ).read_text(encoding="utf-8")
    assert (
        job_dir / "report_versions" / "v2" / "artifacts" / "plots" / "mean_circadian.png"
    ).is_file()


def test_normalize_chat_artifact_links_translates_container_path() -> None:
    job_id = UUID("1db3e835-d47a-4cef-967a-a3131ca5c55e")
    content = "![Profile](/app/jobs/1db3e835-d47a-4cef-967a-a3131ca5c55e/plots/reference.png)"

    assert normalize_chat_artifact_links(content, job_id) == (
        "![Profile](/jobs/1db3e835-d47a-4cef-967a-a3131ca5c55e/plots/reference.png)"
    )


def test_normalize_chat_artifact_links_makes_relative_plot_absolute() -> None:
    job_id = UUID("1db3e835-d47a-4cef-967a-a3131ca5c55e")

    assert normalize_chat_artifact_links("![Profile](plots/reference.png)", job_id) == (
        "![Profile](/jobs/1db3e835-d47a-4cef-967a-a3131ca5c55e/plots/reference.png)"
    )


def test_extract_chat_artifact_images_for_explicit_inline_rendering() -> None:
    job_id = UUID("1db3e835-d47a-4cef-967a-a3131ca5c55e")
    text, images = extract_chat_artifact_images(
        "Created this plot:\n\n![Circadian profile](plots/reference.png)",
        job_id,
    )

    assert text == "Created this plot:"
    assert len(images) == 1
    assert images[0].alt == "Circadian profile"
    assert images[0].url == ("/jobs/1db3e835-d47a-4cef-967a-a3131ca5c55e/plots/reference.png")


@pytest.mark.asyncio
async def test_send_chat_message_cleans_up_ipc_files(
    db_session: AsyncSession,
    test_user: User,
    test_job: Job,
    temp_jobs_dir: Path,
):
    """The request and response files are removed after the turn."""
    _ = test_user
    job_dir = temp_jobs_dir / str(test_job.id)
    job_dir.mkdir()

    with _chat_runner_writing({"output": "ok"}):
        await send_chat_message(db_session, test_job.id, "hi", job_dir)

    assert not (job_dir / _CHAT_REQUEST_FILE).exists()
    assert not (job_dir / _CHAT_RESPONSE_FILE).exists()


@pytest.mark.asyncio
async def test_send_chat_message_raises_on_container_failure(
    db_session: AsyncSession,
    test_user: User,
    test_job: Job,
    temp_jobs_dir: Path,
):
    """A failed container raises and stores no messages."""
    _ = test_user
    job_dir = temp_jobs_dir / str(test_job.id)
    job_dir.mkdir()

    with _chat_runner_failing(RuntimeError("Chat container exited with code 1")):
        with pytest.raises(RuntimeError, match="exited with code 1"):
            await send_chat_message(db_session, test_job.id, "hi", job_dir)

    history = await get_chat_history(db_session, test_job.id)
    assert len(history) == 0


@pytest.mark.asyncio
async def test_send_chat_message_raises_on_error_response(
    db_session: AsyncSession,
    test_user: User,
    test_job: Job,
    temp_jobs_dir: Path,
):
    """An error reply in the response file raises and stores no messages."""
    _ = test_user
    job_dir = temp_jobs_dir / str(test_job.id)
    job_dir.mkdir()

    with _chat_runner_writing({"error": "Chat executor returned no output"}):
        with pytest.raises(RuntimeError, match="Chat executor returned no output"):
            await send_chat_message(db_session, test_job.id, "hi", job_dir)

    history = await get_chat_history(db_session, test_job.id)
    assert len(history) == 0


@pytest.mark.asyncio
async def test_build_chat_request_keeps_system_prompt_small(
    db_session: AsyncSession,
    test_user: User,
    test_job: Job,
    temp_jobs_dir: Path,
):
    """Job context goes in the prompt, never the system prompt (which the claude
    CLI passes as an arg subject to ARG_MAX)."""
    _ = test_user
    from openscientist.knowledge_state import KnowledgeState

    large_ks = KnowledgeState(str(test_job.id), "Q?", 10)
    large_ks.data["findings"] = [{"content": "x" * 50000}]
    job_dir = temp_jobs_dir / str(test_job.id)
    job_dir.mkdir()

    with (
        patch("openscientist.providers.get_provider", return_value=_ChatProvider()),
        patch(
            "openscientist.job_chat.KnowledgeState.load_from_database_sync",
            return_value=large_ks,
        ),
    ):
        request = await _build_chat_request(db_session, test_job.id, "Summarize findings", job_dir)

    system_prompt = request["system_prompt"]
    prompt = request["prompt"]
    assert system_prompt is not None
    assert prompt is not None
    assert len(system_prompt) < 2000
    assert "x" * 1000 not in system_prompt
    assert "x" * 1000 in prompt


@pytest.mark.asyncio
async def test_build_chat_request_codex_folds_guidance(
    db_session: AsyncSession,
    test_user: User,
    test_job: Job,
    temp_jobs_dir: Path,
):
    """Codex has no model override and folds the chat guidance into the system
    prompt (delivered via AGENTS.md, not the Claude-only CLAUDE.md)."""
    _ = test_user
    from tests.helpers import StubCodexProvider

    job_dir = temp_jobs_dir / str(test_job.id)
    job_dir.mkdir()
    assigned_ids = (
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    )
    (job_dir / ".openscientist_skill_manifest.json").write_text(
        json.dumps(
            [
                {
                    "id": assigned_ids[0],
                    "key": "domain--digital-ventilated-cage-analysis",
                    "name": "Digital Ventilated Cage Analysis",
                },
                {
                    "id": assigned_ids[1],
                    "key": "domain--data-science",
                    "name": "data-science",
                },
            ]
        ),
        encoding="utf-8",
    )

    with patch("openscientist.providers.get_provider", return_value=StubCodexProvider()):
        request = await _build_chat_request(db_session, test_job.id, "hi", job_dir)

    system_prompt = request["system_prompt"]
    assert request["model_override"] is None
    assert system_prompt is not None
    assert "OpenScientist Job Chat Assistant" in system_prompt
    assert "domain--digital-ventilated-cage-analysis" in system_prompt
    assert "domain--data-science" in system_prompt
    assert request["assigned_skill_ids"] == list(assigned_ids)


@pytest.mark.asyncio
async def test_run_chat_turn_async_writes_reply(temp_jobs_dir: Path):
    """The container-side turn reads the request, runs the agent with the
    request's system prompt, and writes the reply."""
    job_dir = temp_jobs_dir / "chat-turn"
    job_dir.mkdir()
    (job_dir / _CHAT_REQUEST_FILE).write_text(
        json.dumps({"system_prompt": "You are helpful.", "model_override": None, "prompt": "hi"})
    )

    captured: dict[str, AgentConfig] = {}
    result = IterationResult(
        outcome=TurnOutcome.COMPLETED, output="reply", tool_calls=0, transcript=[]
    )
    with (
        patch("openscientist.providers.get_provider", return_value=_ChatProvider()),
        patch("openscientist.agent.factory.build_agent", _build_agent_recorder(captured, result)),
        patch(
            "openscientist.agent.claude_code_agent.ClaudeCodeAgent.prepare_job_workspace",
            new_callable=AsyncMock,
        ) as prepare_workspace,
    ):
        status = await run_chat_turn_async(job_dir)

    assert status["status"] == "completed"
    response = json.loads((job_dir / _CHAT_RESPONSE_FILE).read_text())
    assert response["output"] == "reply"
    assert captured["config"].system_prompt == "You are helpful."
    assert captured["config"].assigned_skill_ids is None
    assert (job_dir / ".claude" / "CLAUDE.md").exists()
    prepare_workspace.assert_awaited_once_with(use_hypotheses=False)


@pytest.mark.asyncio
async def test_run_chat_turn_async_preserves_codex_skill_assignment(temp_jobs_dir: Path):
    """The container rebuilds Codex with the discovery run's exact skills."""
    from tests.helpers import StubCodexProvider

    job_dir = temp_jobs_dir / "codex-chat-turn"
    job_dir.mkdir()
    assigned_ids = (
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    )
    (job_dir / _CHAT_REQUEST_FILE).write_text(
        json.dumps(
            {
                "system_prompt": "OpenScientist Job Chat Assistant with assigned skills",
                "model_override": None,
                "prompt": "hi",
                "assigned_skill_ids": list(assigned_ids),
            }
        )
    )
    captured: dict[str, AgentConfig] = {}
    result = IterationResult(
        outcome=TurnOutcome.COMPLETED,
        output="Codex chat reply",
        tool_calls=0,
        transcript=[],
    )

    with (
        patch("openscientist.providers.get_provider", return_value=StubCodexProvider()),
        patch("openscientist.agent.factory.build_agent", _build_agent_recorder(captured, result)),
        patch(
            "openscientist.agent.codex_agent.CodexAgent.prepare_job_workspace",
            new_callable=AsyncMock,
        ) as prepare_workspace,
    ):
        status = await run_chat_turn_async(job_dir)

    assert status["status"] == "completed"
    response = json.loads((job_dir / _CHAT_RESPONSE_FILE).read_text())
    assert response["output"] == "Codex chat reply"
    assert not (job_dir / ".claude" / "CLAUDE.md").exists()
    config = captured["config"]
    assert config.assigned_skill_ids == assigned_ids
    assert config.model_override is None
    prepare_workspace.assert_awaited_once_with(use_hypotheses=False)


@pytest.mark.asyncio
async def test_run_chat_turn_async_records_error(temp_jobs_dir: Path):
    """A failed turn is captured as an error in the response file, not raised."""
    job_dir = temp_jobs_dir / "chat-turn-err"
    job_dir.mkdir()
    (job_dir / _CHAT_REQUEST_FILE).write_text(
        json.dumps({"system_prompt": "SP", "model_override": None, "prompt": "hi"})
    )

    captured: dict[str, AgentConfig] = {}
    result = IterationResult(
        outcome=TurnOutcome.FAILED, output="", tool_calls=0, transcript=[], error="boom"
    )
    with (
        patch("openscientist.providers.get_provider", return_value=_ChatProvider()),
        patch("openscientist.agent.factory.build_agent", _build_agent_recorder(captured, result)),
    ):
        status = await run_chat_turn_async(job_dir)

    assert status["status"] == "failed"
    response = json.loads((job_dir / _CHAT_RESPONSE_FILE).read_text())
    assert response["error"] == "boom"
