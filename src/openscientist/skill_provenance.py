"""Evidence-based skill usage summaries for job reports and admin views.

Assignment and usage are deliberately separate: the manifest records which
skills the agent could use, while normalized transcripts provide evidence that
a skill was actually invoked. Claude emits an explicit ``Skill`` tool call.
Codex project skills are implicit, so reading a project ``SKILL.md`` is treated
as the auditable invocation event.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openscientist.transcript import (
    AssistantText,
    ShellExecution,
    ToolCall,
    ToolResult,
    TranscriptEntry,
    load_transcript,
)

_ITERATION_RE = re.compile(r"iter(\d+)_transcript$")
_SKILL_PATH_PATTERNS = (
    re.compile(
        r"(?:^|[/\s\"'=])\.agents/skills/([^/]+)/skill\.md(?:$|[\s\"'])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[/\s\"'=])\.claude/skills/([^/]+)\.md(?:$|[\s\"'])",
        re.IGNORECASE,
    ),
)
_SUMMARY_LIMIT = 1200


@dataclass(frozen=True)
class SkillUsageEvidence:
    """One transcript-backed skill invocation."""

    invocation_id: str
    skill_key: str
    skill_name: str
    source: str
    phase: str
    iteration: int | None
    prompt_summary: str
    instruction_summary: str
    produced_result_summary: str
    success: bool


def _summarize(value: Any, *, limit: int = _SUMMARY_LIMIT) -> str:
    """Return compact, deterministic display text without adding LLM cost."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return " ".join(text.split())[:limit]


def _skill_key_from_text(value: str) -> str | None:
    normalized = value.replace("\\", "/")
    for pattern in _SKILL_PATH_PATTERNS:
        match = pattern.search(normalized)
        if match:
            return match.group(1)
    return None


def _manifest_by_key(job_dir: Path) -> dict[str, dict[str, Any]]:
    path = job_dir / ".openscientist_skill_manifest.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, list):
        return {}
    return {str(item["key"]): item for item in raw if isinstance(item, dict) and item.get("key")}


def _next_assistant_text(transcript: list[TranscriptEntry], start: int) -> str:
    for entry in transcript[start + 1 :]:
        if isinstance(entry, AssistantText) and entry.text.strip():
            return _summarize(entry.text)
    return ""


def extract_skill_usage(
    transcript: list[TranscriptEntry],
    *,
    phase: str,
    iteration: int | None = None,
    skill_names: dict[str, str] | None = None,
) -> list[SkillUsageEvidence]:
    """Extract explicit Claude and implicit Codex skill-use evidence."""
    names = skill_names or {}
    results = {entry.call_id: entry for entry in transcript if isinstance(entry, ToolResult)}
    usages: list[SkillUsageEvidence] = []

    for index, entry in enumerate(transcript):
        skill_key: str | None = None
        source = ""
        prompt = ""
        instruction = ""
        success = True

        if isinstance(entry, ToolCall):
            short_name = entry.tool.split("__")[-1].lower()
            if short_name == "skill":
                raw_key = entry.arguments.get("skill") or entry.arguments.get("name")
                skill_key = str(raw_key) if raw_key else None
                source = "explicit_skill_tool"
                prompt = _summarize(entry.arguments)
            else:
                skill_key = _skill_key_from_text(_summarize(entry.arguments, limit=4000))
                if skill_key:
                    source = "skill_file_read"
                    prompt = _summarize(entry.arguments)
            result = results.get(entry.id)
            if result is not None:
                instruction = _summarize(result.output)
                success = result.success
            invocation_id = entry.id
        elif isinstance(entry, ShellExecution):
            skill_key = _skill_key_from_text(entry.command)
            if skill_key:
                source = "codex_skill_file_read"
                prompt = _summarize(entry.command)
                instruction = _summarize(entry.output)
                success = entry.exit_code in (None, 0)
            invocation_id = entry.id
        else:
            continue

        if not skill_key:
            continue
        usages.append(
            SkillUsageEvidence(
                invocation_id=invocation_id,
                skill_key=skill_key,
                skill_name=names.get(skill_key, skill_key),
                source=source,
                phase=phase,
                iteration=iteration,
                prompt_summary=prompt,
                instruction_summary=instruction,
                produced_result_summary=_next_assistant_text(transcript, index),
                success=success,
            )
        )
    return usages


def build_job_skill_provenance(job_dir: Path) -> dict[str, Any]:
    """Build a render-ready assignment and usage summary from job artifacts."""
    job_dir = Path(job_dir)
    manifest_by_key = _manifest_by_key(job_dir)
    skill_names = {key: str(item.get("name") or key) for key, item in manifest_by_key.items()}
    usages: list[SkillUsageEvidence] = []
    provenance_dir = job_dir / "provenance"
    if provenance_dir.exists():
        for path in sorted(provenance_dir.glob("*_transcript.json")):
            stem = path.stem
            iteration_match = _ITERATION_RE.match(stem)
            iteration = int(iteration_match.group(1)) if iteration_match else None
            phase = "discovery" if iteration is not None else stem.removesuffix("_transcript")
            try:
                transcript = load_transcript(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            usages.extend(
                extract_skill_usage(
                    transcript,
                    phase=phase,
                    iteration=iteration,
                    skill_names=skill_names,
                )
            )

    counts: dict[str, int] = {}
    for usage in usages:
        counts[usage.skill_key] = counts.get(usage.skill_key, 0) + 1
    return {
        "assigned_skills": list(manifest_by_key.values()),
        "used_skills": [
            {
                **manifest_by_key.get(key, {"key": key, "name": skill_names.get(key, key)}),
                "invocations": count,
            }
            for key, count in counts.items()
        ],
        "invocations": [asdict(usage) for usage in usages],
    }


def write_job_skill_provenance(job_dir: Path) -> Path:
    """Refresh the aggregate JSON artifact consumed by report/admin pages."""
    job_dir = Path(job_dir)
    provenance_dir = job_dir / "provenance"
    provenance_dir.mkdir(parents=True, exist_ok=True)
    destination = provenance_dir / "skill_usage.json"
    destination.write_text(
        json.dumps(build_job_skill_provenance(job_dir), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return destination
