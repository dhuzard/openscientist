"""SKILL.md rendering shared by the agents that materialise skills."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from openscientist.database.models import Skill
from openscientist.database.session import AsyncSessionLocal
from openscientist.evidence_librarian import filter_skills_for_plan
from openscientist.prompts import generate_job_claude_md, get_enabled_skills
from openscientist.settings import get_settings

logger = logging.getLogger(__name__)


def _replace_skill_directory(skills_dir: Path) -> None:
    """Remove a prior materialization before writing the current assignment."""
    if skills_dir.exists():
        shutil.rmtree(skills_dir)


def _write_skill_manifest(job_dir: Path, skills: list[Skill]) -> None:
    """Write a non-secret assignment snapshot used by provenance readers."""
    manifest = [
        {
            "id": str(skill.id),
            "key": f"{skill.category}--{skill.slug}",
            "name": skill.name,
            "category": skill.category,
            "slug": skill.slug,
            "version": skill.version if isinstance(skill.version, int) else None,
        }
        for skill in skills
    ]
    (job_dir / ".openscientist_skill_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )


def _write_job_claude_md(claude_dir: Path, *, use_hypotheses: bool = False) -> None:
    """Write the generated discovery JOB CLAUDE.md into ``claude_dir``."""
    try:
        phenix_available = get_settings().phenix.is_available
        dest = claude_dir / "CLAUDE.md"
        dest.write_text(
            generate_job_claude_md(
                use_hypotheses=use_hypotheses, phenix_available=phenix_available
            ),
            encoding="utf-8",
        )
        logger.debug("Wrote job CLAUDE.md to %s (use_hypotheses=%s)", dest, use_hypotheses)
    except Exception as e:
        logger.warning("Failed to write job CLAUDE.md: %s", e)


async def write_skills_to_claude_dir(
    job_dir: Path,
    *,
    use_hypotheses: bool = False,
    skill_ids: tuple[str, ...] | None = None,
) -> None:
    """Write the job's assigned, enabled skills into ``job_dir/.claude/``."""
    claude_dir = job_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    # Write the discovery-agent JOB CLAUDE.md (hypothesis sections conditional)
    _write_job_claude_md(claude_dir, use_hypotheses=use_hypotheses)

    try:
        async with AsyncSessionLocal(thread_safe=True) as session:
            skills = await get_enabled_skills(session, skill_ids)
        skills = filter_skills_for_plan(job_dir, skills)
        skills_dir = claude_dir / "skills"
        _replace_skill_directory(skills_dir)
        _write_skill_manifest(job_dir, skills)
        if not skills:
            logger.info("No assigned, enabled skills to write")
            return
        skills_dir.mkdir(parents=True, exist_ok=True)
        for skill in skills:
            filename = f"{skill.category}--{skill.slug}.md"
            path = skills_dir / filename
            path.write_text(claude_skill_markdown(skill), encoding="utf-8")
        logger.info("Wrote %d skill files to %s", len(skills), skills_dir)
    except Exception as e:
        logger.warning("Failed to write skills to .claude dir: %s", e)


def _yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def claude_skill_markdown(skill: Skill) -> str:
    """Render one enabled skill in Claude's flat Markdown skill layout."""
    header = f"# {skill.name}\n*Category: {skill.category}*\n"
    if skill.description:
        header += f"\n{skill.description}\n"
    return header + "\n" + skill.content


def codex_skill_markdown(skill: Skill) -> str:
    """Render one enabled skill as a codex ``SKILL.md`` (frontmatter + body).

    The ``name`` is capped at 64 chars and ``description`` collapsed to one line
    (max 1024) with a non-empty fallback, since some providers drop a skill whose
    description is empty.
    """
    name = f"{skill.category}--{skill.slug}"[:64]
    description = " ".join((skill.description or "").split())[:1024]
    if not description:
        description = f"{skill.category} skill: {skill.name}"
    frontmatter = f"---\nname: {_yaml_quote(name)}\ndescription: {_yaml_quote(description)}\n---\n"
    return frontmatter + skill.content


def render_skill_md(skill: Skill) -> str:
    """Render a skill in the shared Agent Skills ``SKILL.md`` layout."""
    return codex_skill_markdown(skill)


async def write_skills_to_codex_dir(
    job_dir: Path, *, skill_ids: tuple[str, ...] | None = None
) -> None:
    """Write assigned, enabled skills as codex ``SKILL.md`` files into
    ``job_dir/.agents/skills/``.

    The codex agent runs with the job dir as its cwd (a git repo), so codex
    treats ``.agents/skills/`` as a project skill root: it discovers each
    ``SKILL.md`` and auto-injects a ``## Skills`` summary into the system
    prompt with its own trigger rules. This is how the codex/Ollama agent
    receives skills; the ``.claude/`` path does not apply to it.
    """
    try:
        async with AsyncSessionLocal(thread_safe=True) as session:
            skills = await get_enabled_skills(session, skill_ids)
        skills = filter_skills_for_plan(job_dir, skills)
        skills_root = job_dir / ".agents" / "skills"
        _replace_skill_directory(skills_root)
        _write_skill_manifest(job_dir, skills)
        if not skills:
            logger.info("No assigned, enabled skills to write")
            return
        for skill in skills:
            skill_dir = skills_root / f"{skill.category}--{skill.slug}"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(codex_skill_markdown(skill), encoding="utf-8")
        logger.info("Wrote %d codex skill files to %s", len(skills), skills_root)
    except Exception as e:
        logger.warning("Failed to write skills to .agents dir: %s", e)
