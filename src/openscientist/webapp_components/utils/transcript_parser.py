"""Transcript parsing utilities for the web application."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from openscientist.transcript import (
    CollabAgentToolCall,
    FileChange,
    ShellExecution,
    TaskNotification,
    ToolCall,
    ToolResult,
    TranscriptEntry,
    WebSearch,
)

_HTTP_5XX_RE = re.compile(r"\bHTTP(?:/\S+)?\s+5\d\d\b|\bHTTP\s+5\d\d\b", re.IGNORECASE)
_LEGACY_EXECUTION_ERROR_RE = re.compile(
    r"code execution service unavailable|❌\s*ERROR", re.IGNORECASE
)

# Known OpenScientist tool names (bare names, without MCP server prefix).
_OPENSCIENTIST_TOOL_NAMES = frozenset(
    {
        "execute_code",
        "search_pubmed",
        "update_knowledge_state",
        "add_hypothesis",
        "update_hypothesis",
        "save_iteration_summary",
        "read_document",
        "set_status",
        "set_job_title",
        "set_consensus_answer",
        "run_phenix_tool",
        "compare_structures",
        "parse_alphafold_confidence",
    }
)


@dataclass
class UsageSummary:
    """Summary of tools and skills used in a transcript."""

    tool_counts: dict[str, int] = field(default_factory=dict)
    skill_invocations: list[str] = field(default_factory=list)
    mcp_tool_calls: int = 0
    code_executions: int = 0
    pubmed_searches: int = 0
    findings_recorded: int = 0

    @property
    def skills_used(self) -> list[str]:
        """Deduplicated list of skills invoked."""
        return list(dict.fromkeys(self.skill_invocations))


def _short_tool_name(tool_name: str) -> str:
    """Return short tool name without MCP prefix."""
    return tool_name.split("__")[-1] if "__" in tool_name else tool_name


def _is_openscientist_tool(tool_name: str, short_name: str) -> bool:
    """Return whether a tool belongs to the OpenScientist toolset."""
    return "openscientist" in tool_name.lower() or short_name in _OPENSCIENTIST_TOOL_NAMES


def _collect_tool_results_by_call_id(
    transcript: list[TranscriptEntry],
) -> dict[str, ToolResult]:
    """Index ``ToolResult`` entries by their ``call_id``."""
    return {entry.call_id: entry for entry in transcript if isinstance(entry, ToolResult)}


def _iter_tool_calls(transcript: list[TranscriptEntry]) -> list[ToolCall]:
    """Return every ``ToolCall`` in the transcript, in source order."""
    return [entry for entry in transcript if isinstance(entry, ToolCall)]


def get_action_description(tool_call: ToolCall) -> str:
    """Return a human-readable description for a ``ToolCall``."""
    inp = tool_call.arguments

    if inp.get("description"):
        return str(inp["description"])

    name = tool_call.tool
    if "search_pubmed" in name:
        return f"Search: {inp.get('query', '')}"
    if "update_knowledge_state" in name:
        return f"Finding: {inp.get('title', '')}"
    if "save_iteration_summary" in name:
        return f"Summary: {str(inp.get('summary', ''))[:50]}..."
    if "execute_code" in name:
        return "Code execution"
    if name == "Skill":
        return f"Skill: {inp.get('skill', 'unknown')}"

    return _short_tool_name(name)


def parse_transcript_actions(transcript: list[TranscriptEntry]) -> list[dict[str, Any]]:
    """Extract OpenScientist tool actions paired with their results.

    Returns one dict per ``ToolCall`` whose tool belongs to the
    OpenScientist toolset, with the matched ``ToolResult`` (if any)
    folded in. The shape is stable for UI consumers.
    """
    actions: list[dict[str, Any]] = []
    results = _collect_tool_results_by_call_id(transcript)

    for call in _iter_tool_calls(transcript):
        short_name = _short_tool_name(call.tool)
        if not _is_openscientist_tool(call.tool, short_name):
            continue

        result = results.get(call.id)
        actions.append(
            {
                "tool_name": call.tool,
                "short_name": short_name,
                "description": get_action_description(call),
                "input": call.arguments,
                "result": result.output if result is not None else "",
                "success": result.success if result is not None else True,
            }
        )

    return actions


def extract_agent_activity(transcript: list[TranscriptEntry]) -> list[dict[str, Any]]:
    """Return all troubleshooting-relevant agent actions in source order.

    Unlike :func:`parse_transcript_actions`, this includes Codex shell commands,
    file changes, web searches, and collaboration calls. It also recognises
    legacy ``execute_code`` failures that were returned as successful MCP text
    before protocol-level ``ToolError`` reporting was introduced.
    """
    activity: list[dict[str, Any]] = []
    results = _collect_tool_results_by_call_id(transcript)

    for entry in transcript:
        if isinstance(entry, ToolCall):
            result = results.get(entry.id)
            output = result.output if result is not None else ""
            success: bool | None = result.success if result is not None else None
            error = result.error_message if result is not None else None
            short_name = _short_tool_name(entry.tool)
            legacy_error = bool(
                short_name == "execute_code" and _LEGACY_EXECUTION_ERROR_RE.search(output)
            )
            if legacy_error:
                success = False
                error = error or output
            activity.append(
                {
                    "kind": "tool",
                    "name": short_name,
                    "description": get_action_description(entry),
                    "input": entry.arguments,
                    "output": output,
                    "success": success,
                    "status": (
                        result.status
                        if result is not None and result.status
                        else "running"
                        if result is None
                        else "completed"
                    ),
                    "error": error or "",
                    "http_5xx": bool(_HTTP_5XX_RE.search(f"{error or ''}\n{output}")),
                    "legacy_error_response": legacy_error,
                }
            )
        elif isinstance(entry, ShellExecution):
            success = entry.exit_code == 0 if entry.exit_code is not None else None
            activity.append(
                {
                    "kind": "shell",
                    "name": "shell",
                    "description": entry.command,
                    "input": {"command": entry.command},
                    "output": entry.output,
                    "success": success,
                    "status": entry.status or ("completed" if success is not None else "running"),
                    "error": entry.output if success is False else "",
                    "http_5xx": bool(_HTTP_5XX_RE.search(entry.output)),
                    "legacy_error_response": False,
                }
            )
        elif isinstance(entry, FileChange):
            activity.append(
                {
                    "kind": "file_change",
                    "name": f"file_{entry.kind}",
                    "description": entry.path,
                    "input": {"path": entry.path, "kind": entry.kind},
                    "output": entry.diff or "",
                    "success": entry.success,
                    "status": entry.status or "completed",
                    "error": "" if entry.success else (entry.diff or "File change failed"),
                    "http_5xx": False,
                    "legacy_error_response": False,
                }
            )
        elif isinstance(entry, WebSearch):
            activity.append(
                {
                    "kind": "web_search",
                    "name": "web_search",
                    "description": entry.query,
                    "input": {"query": entry.query, "action": entry.action},
                    "output": "",
                    "success": True,
                    "status": "completed",
                    "error": "",
                    "http_5xx": False,
                    "legacy_error_response": False,
                }
            )
        elif isinstance(entry, CollabAgentToolCall):
            activity.append(
                {
                    "kind": "collaboration",
                    "name": entry.tool or "collaboration",
                    "description": entry.prompt or "Subagent activity",
                    "input": {"prompt": entry.prompt, "model": entry.model},
                    "output": "",
                    "success": None
                    if entry.status in {None, "inProgress"}
                    else entry.status == "completed",
                    "status": entry.status or "running",
                    "error": "",
                    "http_5xx": False,
                    "legacy_error_response": False,
                }
            )

    return activity


def extract_agent_notifications(transcript: list[TranscriptEntry]) -> list[dict[str, str]]:
    """Extract timeout/failure lifecycle notifications for troubleshooting UI."""
    return [
        {
            "status": entry.status,
            "summary": entry.summary,
            "task_id": entry.task_id,
        }
        for entry in transcript
        if isinstance(entry, TaskNotification)
    ]


def extract_usage_summary(transcript: list[TranscriptEntry]) -> UsageSummary:
    """Tally tool / skill usage across the transcript."""
    summary = UsageSummary()

    for call in _iter_tool_calls(transcript):
        tool_name = call.tool
        short_name = _short_tool_name(tool_name)
        summary.tool_counts[short_name] = summary.tool_counts.get(short_name, 0) + 1

        if "execute_code" in tool_name:
            summary.code_executions += 1
            summary.mcp_tool_calls += 1
        elif "search_pubmed" in tool_name:
            summary.pubmed_searches += 1
            summary.mcp_tool_calls += 1
        elif "update_knowledge_state" in tool_name:
            summary.findings_recorded += 1
            summary.mcp_tool_calls += 1
        elif _is_openscientist_tool(tool_name, short_name):
            summary.mcp_tool_calls += 1

        if tool_name == "Skill":
            skill_name = call.arguments.get("skill", "")
            if skill_name:
                summary.skill_invocations.append(skill_name)

    return summary
