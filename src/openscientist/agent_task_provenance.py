"""Evidence-backed runtime model and subagent task provenance."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openscientist.transcript import (
    AssistantText,
    CollabAgentToolCall,
    TaskNotification,
    TaskProgress,
    TaskStarted,
    load_transcript,
)

_RUNTIME_MODEL_FILE = "model_runtime.json"
_ITERATION_RE = re.compile(r"iter(\d+)_transcript$")
_SUMMARY_LIMIT = 1200


def _summary(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return " ".join(text.split())[:_SUMMARY_LIMIT]


def write_job_model_runtime(
    job_dir: Path,
    *,
    provider: str,
    model: str,
    backend: str,
    context_window_tokens: int | None,
) -> Path:
    """Persist the model selected for this job before its first LLM turn."""
    provenance_dir = Path(job_dir) / "provenance"
    provenance_dir.mkdir(parents=True, exist_ok=True)
    destination = provenance_dir / _RUNTIME_MODEL_FILE
    destination.write_text(
        json.dumps(
            {
                "provider": provider,
                "model": model,
                "backend": backend,
                "context_window_tokens": context_window_tokens,
                "resolved_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return destination


def _load_runtime(job_dir: Path) -> dict[str, Any]:
    path = Path(job_dir) / "provenance" / _RUNTIME_MODEL_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _transcript_location(path: Path) -> tuple[str, int | None]:
    match = _ITERATION_RE.match(path.stem)
    if match:
        return "discovery", int(match.group(1))
    return path.stem.removesuffix("_transcript"), None


def _new_subagent(
    *,
    agent_id: str,
    phase: str,
    iteration: int | None,
    backend: str,
    runtime_model: str,
) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "phase": phase,
        "iteration": iteration,
        "backend": backend,
        "task_type": "subagent",
        "description": "",
        "prompt_summary": "",
        "result_summary": "",
        "status": "unknown",
        "model": runtime_model or "unknown",
        "model_source": "inherited" if runtime_model else "not_exposed",
        "reasoning_effort": None,
        "total_tokens": None,
        "token_attribution": "not_exposed",
        "parent_tool_use_id": None,
        "events": 0,
    }


def _merge_codex_event(
    tasks: dict[str, dict[str, Any]],
    entry: CollabAgentToolCall,
    *,
    phase: str,
    iteration: int | None,
    runtime_model: str,
) -> None:
    raw = entry.raw
    receiver_ids = entry.receiver_thread_ids or raw.get("receiver_thread_ids") or []
    if not isinstance(receiver_ids, list):
        receiver_ids = []
    state_ids = list((entry.agents_states or {}).keys())
    agent_ids = [str(value) for value in receiver_ids or state_ids or [entry.id]]

    for agent_id in agent_ids:
        task = tasks.setdefault(
            agent_id,
            _new_subagent(
                agent_id=agent_id,
                phase=phase,
                iteration=iteration,
                backend="codex",
                runtime_model=runtime_model,
            ),
        )
        task["events"] += 1
        if entry.prompt:
            task["prompt_summary"] = _summary(entry.prompt)
            task["description"] = _summary(entry.prompt)
        if entry.model:
            task["model"] = entry.model
            task["model_source"] = "requested_by_spawn"
        if entry.reasoning_effort:
            task["reasoning_effort"] = entry.reasoning_effort
        if entry.tool:
            task["task_type"] = entry.tool
        state = (entry.agents_states or {}).get(agent_id)
        if isinstance(state, dict):
            task["status"] = str(state.get("status") or entry.status or "unknown")
            if state.get("message"):
                task["result_summary"] = _summary(state["message"])
        elif entry.status:
            task["status"] = entry.status


def _merge_claude_entries(
    tasks: dict[str, dict[str, Any]],
    entries: list[Any],
    *,
    phase: str,
    iteration: int | None,
    runtime_model: str,
) -> None:
    parent_to_task: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, TaskStarted):
            task = tasks.setdefault(
                entry.task_id,
                _new_subagent(
                    agent_id=entry.task_id,
                    phase=phase,
                    iteration=iteration,
                    backend="claude_code",
                    runtime_model=runtime_model,
                ),
            )
            task.update(
                {
                    "task_type": entry.task_type or "subagent",
                    "description": _summary(entry.description),
                    "prompt_summary": _summary(entry.description),
                    "status": "running",
                    "parent_tool_use_id": entry.parent_tool_use_id,
                }
            )
            task["events"] += 1
            if entry.parent_tool_use_id:
                parent_to_task[entry.parent_tool_use_id] = entry.task_id
        elif isinstance(entry, (TaskProgress, TaskNotification)):
            task = tasks.setdefault(
                entry.task_id,
                _new_subagent(
                    agent_id=entry.task_id,
                    phase=phase,
                    iteration=iteration,
                    backend="claude_code",
                    runtime_model=runtime_model,
                ),
            )
            task["events"] += 1
            usage = entry.usage or {}
            if isinstance(usage.get("total_tokens"), int):
                task["total_tokens"] = usage["total_tokens"]
                task["token_attribution"] = "provider_task_total"
            if isinstance(entry, TaskProgress):
                task["description"] = _summary(entry.description) or task["description"]
                task["status"] = "running"
            else:
                task["status"] = entry.status
                task["result_summary"] = _summary(entry.summary)
        elif isinstance(entry, AssistantText) and entry.parent_tool_use_id:
            task_id = parent_to_task.get(entry.parent_tool_use_id)
            if task_id and entry.model:
                tasks[task_id]["model"] = entry.model
                tasks[task_id]["model_source"] = "assistant_message"


def build_job_agent_task_provenance(job_dir: Path) -> dict[str, Any]:
    """Build runtime model identity and subagent evidence from saved artifacts."""
    job_dir = Path(job_dir)
    runtime = _load_runtime(job_dir)
    runtime_model = str(runtime.get("model") or "")
    tasks: dict[str, dict[str, Any]] = {}
    provenance_dir = job_dir / "provenance"

    if provenance_dir.exists():
        for path in sorted(provenance_dir.glob("*_transcript.json")):
            phase, iteration = _transcript_location(path)
            try:
                entries = load_transcript(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            _merge_claude_entries(
                tasks,
                entries,
                phase=phase,
                iteration=iteration,
                runtime_model=runtime_model,
            )
            for entry in entries:
                if isinstance(entry, CollabAgentToolCall):
                    _merge_codex_event(
                        tasks,
                        entry,
                        phase=phase,
                        iteration=iteration,
                        runtime_model=runtime_model,
                    )

    return {
        "runtime": runtime,
        "subagents": list(tasks.values()),
    }
