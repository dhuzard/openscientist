"""Per-job skill assignment and usage provenance tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from starlette.datastructures import FormData

from openscientist.agent.skills import (
    write_skills_to_claude_dir,
    write_skills_to_codex_dir,
)
from openscientist.api.endpoints.jobs import JobCreate, _extract_job_payload_from_form
from openscientist.database.models import Skill
from openscientist.job_manager import _db_validate_skill_ids
from openscientist.skill_provenance import (
    build_job_skill_provenance,
    extract_skill_usage,
)
from openscientist.transcript import (
    AssistantText,
    ShellExecution,
    ToolCall,
    ToolResult,
    TranscriptEntry,
)


def _skill(*, skill_id: UUID, name: str, category: str, slug: str) -> Skill:
    return Skill(
        id=skill_id,
        name=name,
        category=category,
        slug=slug,
        description=f"{name} description",
        content=f"# {name}\nInstructions",
        tags=[],
        content_hash="a" * 64,
        is_enabled=True,
        version=2,
    )


@pytest.mark.asyncio
async def test_codex_materializes_only_assigned_skills_and_removes_stale(tmp_path: Path) -> None:
    selected_id = uuid4()
    selected = _skill(
        skill_id=selected_id,
        name="Selected",
        category="analysis",
        slug="selected",
    )
    stale = tmp_path / ".agents" / "skills" / "old--stale" / "SKILL.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale", encoding="utf-8")

    with (
        patch("openscientist.agent.skills.AsyncSessionLocal"),
        patch(
            "openscientist.agent.skills.get_enabled_skills",
            new=AsyncMock(return_value=[selected]),
        ) as get_skills,
    ):
        await write_skills_to_codex_dir(tmp_path, skill_ids=(str(selected_id),))

    get_skills.assert_awaited_once()
    assert get_skills.await_args is not None
    assert get_skills.await_args.args[1] == (str(selected_id),)
    assert not stale.exists()
    assert (tmp_path / ".agents" / "skills" / "analysis--selected" / "SKILL.md").exists()
    manifest = json.loads(
        (tmp_path / ".openscientist_skill_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest[0]["id"] == str(selected_id)
    assert manifest[0]["version"] == 2


@pytest.mark.asyncio
async def test_explicit_empty_assignment_materializes_no_claude_skills(tmp_path: Path) -> None:
    stale = tmp_path / ".claude" / "skills" / "old--stale.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale", encoding="utf-8")

    with (
        patch("openscientist.agent.skills.AsyncSessionLocal"),
        patch(
            "openscientist.agent.skills.get_enabled_skills",
            new=AsyncMock(return_value=[]),
        ) as get_skills,
    ):
        await write_skills_to_claude_dir(tmp_path, skill_ids=())

    assert get_skills.await_args is not None
    assert get_skills.await_args.args[1] == ()
    assert not stale.exists()
    assert (
        json.loads((tmp_path / ".openscientist_skill_manifest.json").read_text(encoding="utf-8"))
        == []
    )


@pytest.mark.asyncio
async def test_validate_skill_ids_rejects_disabled_or_unknown() -> None:
    available = uuid4()
    unavailable = uuid4()
    scalars = MagicMock()
    scalars.all.return_value = [available]
    result = MagicMock()
    result.scalars.return_value = scalars
    session = AsyncMock()
    session.execute.return_value = result
    context = AsyncMock()
    context.__aenter__.return_value = session

    with patch("openscientist.job_manager.AsyncSessionLocal", return_value=context):
        with pytest.raises(ValueError, match=str(unavailable)):
            await _db_validate_skill_ids([str(available), str(unavailable)])


def test_extracts_explicit_claude_skill_prompt_and_produced_result() -> None:
    transcript: list[TranscriptEntry] = [
        ToolCall(
            id="call-1",
            tool="Skill",
            arguments={"skill": "analysis--profile", "prompt": "Analyze the rhythm"},
        ),
        ToolResult(call_id="call-1", output="Loaded profile workflow", success=True),
        AssistantText(text="The profile has a nocturnal activity peak."),
    ]

    usages = extract_skill_usage(
        transcript,
        phase="discovery",
        iteration=2,
        skill_names={"analysis--profile": "Circadian profile"},
    )

    assert len(usages) == 1
    usage = usages[0]
    assert usage.skill_name == "Circadian profile"
    assert usage.source == "explicit_skill_tool"
    assert "Analyze the rhythm" in usage.prompt_summary
    assert usage.instruction_summary == "Loaded profile workflow"
    assert "nocturnal activity peak" in usage.produced_result_summary


def test_extracts_implicit_codex_skill_read_and_builds_job_summary(tmp_path: Path) -> None:
    manifest = [
        {
            "id": str(uuid4()),
            "key": "analysis--profile",
            "name": "Circadian profile",
            "category": "analysis",
            "slug": "profile",
            "version": 1,
        }
    ]
    (tmp_path / ".openscientist_skill_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    provenance = tmp_path / "provenance"
    provenance.mkdir()
    transcript: list[TranscriptEntry] = [
        ShellExecution(
            id="shell-1",
            command="Get-Content .agents/skills/analysis--profile/SKILL.md",
            output="# Circadian profile Instructions",
            exit_code=0,
        ),
        AssistantText(text="Applied the profile workflow to the activity data."),
    ]
    from openscientist.transcript import save_transcript

    save_transcript(provenance / "iter3_transcript.json", transcript)
    summary = build_job_skill_provenance(tmp_path)

    assert summary["assigned_skills"] == manifest
    assert summary["used_skills"][0]["key"] == "analysis--profile"
    assert summary["used_skills"][0]["invocations"] == 1
    invocation = summary["invocations"][0]
    assert invocation["source"] == "codex_skill_file_read"
    assert invocation["iteration"] == 3
    assert "Applied the profile workflow" in invocation["produced_result_summary"]


def test_api_job_create_accepts_explicit_or_empty_skill_assignment() -> None:
    first = uuid4()
    second = uuid4()
    explicit = JobCreate(
        research_question="How should this dataset be analyzed?",
        skill_ids=[first, second],
    )
    no_skills = JobCreate(
        research_question="How should this dataset be analyzed?",
        skill_ids=[],
    )

    assert explicit.skill_ids == [first, second]
    assert no_skills.skill_ids == []
    assert JobCreate(research_question="Legacy behavior").skill_ids is None


def test_multipart_job_create_collects_repeated_skill_ids() -> None:
    first = uuid4()
    second = uuid4()
    form = FormData(
        [
            ("research_question", "How should this dataset be analyzed?"),
            ("skill_ids", str(first)),
            ("skill_ids", str(second)),
        ]
    )

    payload = _extract_job_payload_from_form(form)

    assert payload["skill_ids"] == [str(first), str(second)]
