"""Runtime model identity and subagent provenance tests."""

from __future__ import annotations

from pathlib import Path

from openscientist.agent_task_provenance import (
    build_job_agent_task_provenance,
    write_job_model_runtime,
)
from openscientist.transcript import (
    AssistantText,
    CollabAgentToolCall,
    TaskNotification,
    TaskStarted,
    save_transcript,
)


def test_codex_subagent_inherits_runtime_model_and_does_not_invent_tokens(
    tmp_path: Path,
) -> None:
    write_job_model_runtime(
        tmp_path,
        provider="OpenAI API",
        model="gpt-5.5",
        backend="codex",
        context_window_tokens=1_050_000,
    )
    provenance = tmp_path / "provenance"
    save_transcript(
        provenance / "iter2_transcript.json",
        [
            CollabAgentToolCall(
                id="spawn-1",
                prompt="Analyze the circadian profile",
                receiver_thread_ids=["child-thread"],
                sender_thread_id="parent-thread",
                tool="spawnAgent",
                status="completed",
                agents_states={
                    "child-thread": {
                        "status": "completed",
                        "message": "Nocturnal peak confirmed",
                    }
                },
            )
        ],
    )

    result = build_job_agent_task_provenance(tmp_path)

    assert result["runtime"]["model"] == "gpt-5.5"
    task = result["subagents"][0]
    assert task["agent_id"] == "child-thread"
    assert task["model"] == "gpt-5.5"
    assert task["model_source"] == "inherited"
    assert task["total_tokens"] is None
    assert task["token_attribution"] == "not_exposed"
    assert task["iteration"] == 2
    assert "Nocturnal peak" in task["result_summary"]


def test_codex_subagent_records_requested_model_override(tmp_path: Path) -> None:
    write_job_model_runtime(
        tmp_path,
        provider="OpenAI API",
        model="gpt-5.5",
        backend="codex",
        context_window_tokens=1_050_000,
    )
    provenance = tmp_path / "provenance"
    save_transcript(
        provenance / "report_transcript.json",
        [
            CollabAgentToolCall(
                id="spawn-2",
                prompt="Critique the report",
                model="gpt-5.5-mini",
                reasoning_effort="high",
                receiver_thread_ids=["critic-thread"],
                tool="spawnAgent",
                status="completed",
            )
        ],
    )

    task = build_job_agent_task_provenance(tmp_path)["subagents"][0]
    assert task["model"] == "gpt-5.5-mini"
    assert task["model_source"] == "requested_by_spawn"
    assert task["reasoning_effort"] == "high"
    assert task["phase"] == "report"


def test_claude_task_uses_provider_task_tokens_and_assistant_model(tmp_path: Path) -> None:
    write_job_model_runtime(
        tmp_path,
        provider="Anthropic API",
        model="claude-sonnet-default",
        backend="claude_code",
        context_window_tokens=200_000,
    )
    provenance = tmp_path / "provenance"
    save_transcript(
        provenance / "iter4_transcript.json",
        [
            TaskStarted(
                task_id="task-1",
                description="Review statistical assumptions",
                task_type="review",
                parent_tool_use_id="tool-parent",
            ),
            AssistantText(
                text="Reviewing assumptions",
                model="claude-opus-exact",
                parent_tool_use_id="tool-parent",
            ),
            TaskNotification(
                task_id="task-1",
                status="completed",
                summary="Two assumptions need sensitivity checks",
                output_file="",
                usage={"total_tokens": 4321, "tool_uses": 2, "duration_ms": 1000},
            ),
        ],
    )

    task = build_job_agent_task_provenance(tmp_path)["subagents"][0]
    assert task["model"] == "claude-opus-exact"
    assert task["model_source"] == "assistant_message"
    assert task["total_tokens"] == 4321
    assert task["token_attribution"] == "provider_task_total"
    assert task["status"] == "completed"
