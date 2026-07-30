"""
Job chat service for interactive Q&A about OpenScientist jobs.

Allows users to ask questions about their job results, findings, and
analysis process. Uses ClaudeCodeAgent for responses, giving the agent
access to tools (execute_code, search_pubmed, etc.) for follow-up analysis.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openscientist.database.models import Job, JobChatMessage
from openscientist.database.session import AsyncSessionLocal
from openscientist.knowledge_state import KnowledgeState
from openscientist.report.revisions import (
    ReportFigure,
    ReportRevision,
    capture_report_snapshot,
    record_report_revision,
    update_report_markdown,
)

logger = logging.getLogger(__name__)

_RELATIVE_ARTIFACT_LINK_RE = re.compile(
    r"(?P<prefix>\]\(|\bsrc=[\"'])"
    r"(?P<path>(?:\./)?(?:plots|provenance)/[^)\"'\s]+)"
)
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)"
)
_PLOT_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".svg"})


@dataclass(frozen=True)
class _ArtifactSignature:
    size: int
    modified_ns: int


@dataclass(frozen=True)
class ChatArtifactImage:
    """A local job image extracted for explicit inline rendering."""

    alt: str
    url: str


@dataclass(frozen=True)
class _AssignedSkill:
    skill_id: str
    key: str
    name: str


def _load_assigned_skills(job_dir: Path) -> tuple[_AssignedSkill, ...]:
    """Recover the discovery job's exact skill assignment for follow-up chat."""
    path = job_dir / ".openscientist_skill_manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, list):
        return ()
    assigned: list[_AssignedSkill] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        skill_id = item.get("id")
        key = item.get("key")
        if not isinstance(skill_id, str) or not skill_id.strip():
            continue
        if not isinstance(key, str) or not key.strip():
            continue
        name = item.get("name")
        assigned.append(
            _AssignedSkill(
                skill_id=skill_id.strip(),
                key=key.strip(),
                name=name.strip() if isinstance(name, str) and name.strip() else key.strip(),
            )
        )
    return tuple(assigned)


def _assigned_skills_prompt(skills: tuple[_AssignedSkill, ...]) -> str:
    if not skills:
        return ""
    entries = "\n".join(f"- `{skill.key}` ({skill.name})" for skill in skills)
    return (
        "\n\n## Assigned skills carried forward from discovery\n"
        "These skills remain mandatory in Chat. Before follow-up analysis, load and "
        "follow every assigned skill relevant to the request; companion-skill rules "
        "inside them still apply.\n"
        f"{entries}"
    )


def extract_chat_artifact_images(
    content: str,
    job_id: UUID | str,
) -> tuple[str, tuple[ChatArtifactImage, ...]]:
    """Extract safe local image links so the UI can render them explicitly."""
    normalized = normalize_chat_artifact_links(content, job_id)
    allowed_prefix = f"/jobs/{job_id}/"
    images: list[ChatArtifactImage] = []
    seen: set[str] = set()

    def _extract(match: re.Match[str]) -> str:
        url = match.group("url")
        if not url.startswith(allowed_prefix):
            return match.group(0)
        relative = url.removeprefix(allowed_prefix)
        if not relative.startswith(("plots/", "provenance/")):
            return match.group(0)
        if Path(relative).suffix.lower() not in _PLOT_SUFFIXES:
            return match.group(0)
        if url not in seen:
            seen.add(url)
            alt = match.group("alt").strip() or Path(relative).stem.replace("_", " ").title()
            images.append(ChatArtifactImage(alt=alt, url=url))
        return ""

    remaining = _MARKDOWN_IMAGE_RE.sub(_extract, normalized)
    return remaining.strip(), tuple(images)


def _snapshot_plot_artifacts(job_dir: Path) -> dict[str, _ArtifactSignature]:
    snapshot: dict[str, _ArtifactSignature] = {}
    for dirname in ("plots", "provenance"):
        directory = job_dir / dirname
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _PLOT_SUFFIXES:
                continue
            stat = path.stat()
            snapshot[path.relative_to(job_dir).as_posix()] = _ArtifactSignature(
                size=stat.st_size,
                modified_ns=stat.st_mtime_ns,
            )
    return snapshot


def _load_plot_metadata(job_dir: Path, relative_path: str) -> dict[str, Any]:
    path = Path(relative_path)
    direct_metadata = job_dir / path.with_suffix(".json")
    candidates = [direct_metadata]
    provenance_dir = job_dir / "provenance"
    if path.parts and path.parts[0] == "plots" and provenance_dir.is_dir():
        candidates.extend(provenance_dir.glob("*.json"))

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if candidate == direct_metadata:
            return payload
        also_saved_as = str(payload.get("also_saved_as", "")).replace("\\", "/")
        if also_saved_as == relative_path:
            return payload
    return {}


def _plot_title(relative_path: str, metadata: dict[str, Any]) -> str:
    explicit = metadata.get("title")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return Path(relative_path).stem.replace("_", " ").replace("-", " ").title()


def _plot_caption(title: str, metadata: dict[str, Any]) -> str:
    for key in ("description", "caption"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{title}, generated during a follow-up analysis in job chat."


def _changed_report_figures(
    job_dir: Path,
    before: dict[str, _ArtifactSignature],
) -> tuple[ReportFigure, ...]:
    after = _snapshot_plot_artifacts(job_dir)
    changed = [path for path, signature in after.items() if before.get(path) != signature]
    # Prefer descriptive plots/ names. Provenance copies are included only when
    # they are the sole location for their bytes.
    changed.sort(key=lambda path: (not path.startswith("plots/"), path))
    selected: list[ReportFigure] = []
    seen_content: dict[bytes, int] = {}
    for relative_path in changed:
        source = job_dir / relative_path
        try:
            digest = hashlib.sha256(source.read_bytes()).digest()
        except OSError:
            continue
        existing_index = seen_content.get(digest)
        if existing_index is not None:
            existing = selected[existing_index]
            selected[existing_index] = ReportFigure(
                relative_path=existing.relative_path,
                title=existing.title,
                caption=existing.caption,
                aliases=(*existing.aliases, relative_path),
            )
            continue
        seen_content[digest] = len(selected)
        metadata = _load_plot_metadata(job_dir, relative_path)
        title = _plot_title(relative_path, metadata)
        selected.append(
            ReportFigure(
                relative_path=relative_path,
                title=title,
                caption=_plot_caption(title, metadata),
            )
        )
    return tuple(selected)


def _contains_inline_image(content: str, relative_path: str) -> bool:
    filename = re.escape(Path(relative_path).name)
    return bool(re.search(rf"!\[[^\]]*\]\([^)]*{filename}[^)]*\)", content))


def _append_chat_update_summary(
    content: str,
    job_id: UUID,
    figures: tuple[ReportFigure, ...],
    revision: ReportRevision | None,
) -> str:
    additions: list[str] = []
    for figure in figures:
        if not _contains_inline_image(content, figure.relative_path):
            additions.append(f"![{figure.title}](/jobs/{job_id}/{figure.relative_path})")

    if revision is not None:
        additions.extend(
            [
                f"**Scientific Report updated — version v{revision.version}.**",
                f"Location: **Scientific Report → {revision.section}**",
            ]
        )
        if revision.figures:
            additions.append(
                "Accompanying text: "
                + " ".join(figure.caption for figure in revision.figures)
            )
    if not additions:
        return content
    return content.rstrip() + "\n\n" + "\n\n".join(additions)


async def _render_report_outputs(job_dir: Path) -> None:
    """Refresh HTML/PDF without failing the chat if an optional renderer fails."""
    try:
        from openscientist.orchestrator.discovery import _try_generate_report_pdf

        await _try_generate_report_pdf(job_dir / "final_report.md")
    except Exception:
        logger.exception("Failed to render updated chat report outputs in %s", job_dir)


def normalize_chat_artifact_links(content: str, job_id: UUID | str) -> str:
    """Translate agent-container artifact paths into browser-visible job URLs.

    Chat agents work below ``/app/jobs`` or ``/agent/jobs``, while NiceGUI
    exposes the same files below ``/jobs``.  Also make bare ``plots/...`` and
    ``provenance/...`` Markdown/HTML targets absolute so they do not resolve
    relative to the job-detail page.
    """
    job_id_text = str(job_id)
    normalized = content
    for container_root in ("/app/jobs", "/agent/jobs"):
        normalized = normalized.replace(
            f"{container_root}/{job_id_text}/",
            f"/jobs/{job_id_text}/",
        )

    def _replace_relative(match: re.Match[str]) -> str:
        path = match.group("path").removeprefix("./")
        return f"{match.group('prefix')}/jobs/{job_id_text}/{path}"

    return _RELATIVE_ARTIFACT_LINK_RE.sub(_replace_relative, normalized)


async def _load_research_question_from_db(job_id: str) -> str | None:
    try:
        async with AsyncSessionLocal(thread_safe=True) as session:
            result = await session.execute(
                select(Job.research_question).where(Job.id == UUID(job_id))
            )
            value = result.scalar_one_or_none()
        if isinstance(value, str) and value.strip():
            return value
    except ValueError:
        logger.debug("Skipping DB lookup for non-UUID job id: %s", job_id)
    except Exception as e:
        logger.warning("Failed to load DB context for job %s: %s", job_id, e)
    return None


def _append_research_question(parts: list[str], question: str) -> None:
    parts.append(f"# Research Question\n{question}\n")


def _extract_research_question_from_ks(ks: dict[str, Any]) -> str | None:
    ks_config = ks.get("config", {})
    if not isinstance(ks_config, dict):
        return None
    question = ks_config.get("research_question")
    if isinstance(question, str) and question.strip():
        return question.strip()
    return None


def _append_findings(parts: list[str], findings: list[Any]) -> None:
    if not findings:
        return
    parts.append("# Findings")
    for i, finding in enumerate(findings, 1):
        if not isinstance(finding, dict):
            continue
        importance = finding.get("importance", "unknown")
        confidence = finding.get("confidence", "unknown")
        parts.append(f"\n## Finding {i} (Importance: {importance}, Confidence: {confidence})")
        finding_text = finding.get("content", finding.get("title", ""))
        parts.append(finding_text if isinstance(finding_text, str) else str(finding_text))

        evidence = finding.get("evidence", [])
        if evidence:
            parts.append("\nEvidence:")
            parts.extend(f"- {ev}" for ev in evidence)
    parts.append("")


def _append_hypotheses(parts: list[str], hypotheses: list[Any]) -> None:
    if not hypotheses:
        return
    parts.append("# Hypotheses")
    for i, hyp in enumerate(hypotheses, 1):
        if not isinstance(hyp, dict):
            continue
        status = hyp.get("status", "unknown")
        parts.append(f"\n## Hypothesis {i} (Status: {status})")
        hypothesis_text = hyp.get("hypothesis", hyp.get("statement", ""))
        parts.append(hypothesis_text if isinstance(hypothesis_text, str) else str(hypothesis_text))

        rationale = hyp.get("rationale")
        if rationale:
            parts.append(f"\nRationale: {rationale}")
    parts.append("")


def _append_literature(parts: list[str], literature: list[Any]) -> None:
    if not literature:
        return
    parts.append("# Literature Reviewed")
    for i, lit in enumerate(literature, 1):
        if not isinstance(lit, dict):
            continue
        title = lit.get("title", "Unknown")
        relevance = lit.get("relevance_score", "unknown")
        parts.append(f"\n## Paper {i} (Relevance: {relevance})")
        parts.append(f"Title: {title}")

        key_findings = lit.get("key_findings", [])
        if key_findings:
            parts.append("Key findings:")
            parts.extend(f"- {kf}" for kf in key_findings)
    parts.append("")


def _append_iteration_summaries(parts: list[str], summaries: list[Any]) -> None:
    if not summaries:
        return
    parts.append("# Analysis Progress")
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        iteration = summary.get("iteration", 0)
        strapline = summary.get("strapline", "")
        summary_text = summary.get("summary", "")
        parts.append(f"\n## Iteration {iteration}: {strapline}")
        parts.append(summary_text)
    parts.append("")


def _load_knowledge_state(job_id: str) -> dict[str, Any] | None:
    try:
        payload = KnowledgeState.load_from_database_sync(job_id).to_dict()
    except Exception as e:
        logger.warning("Failed to load knowledge state for job %s: %s", job_id, e)
        return None
    return payload if isinstance(payload, dict) else None


async def load_job_context(job_id: str) -> str:
    """
    Load comprehensive job context for LLM chat.

    Includes research question, findings, hypotheses, literature, and
    current analysis state.

    Args:
        job_id: Job ID

    Returns:
        Formatted context string for LLM
    """
    context_parts: list[str] = []
    research_question = await _load_research_question_from_db(job_id)
    if research_question:
        _append_research_question(context_parts, research_question)

    ks = _load_knowledge_state(job_id)
    if not ks:
        return "\n".join(context_parts)

    if not context_parts:
        fallback_question = _extract_research_question_from_ks(ks)
        if fallback_question:
            _append_research_question(context_parts, fallback_question)

    _append_findings(context_parts, ks.get("findings", []))
    _append_hypotheses(context_parts, ks.get("hypotheses", []))
    _append_literature(context_parts, ks.get("literature", []))
    _append_iteration_summaries(context_parts, ks.get("iteration_summaries", []))
    return "\n".join(context_parts)


async def get_chat_history(
    session: AsyncSession,
    job_id: UUID,
    limit: int = 50,
) -> list[JobChatMessage]:
    """
    Get chat message history for a job.

    Args:
        session: Database session
        job_id: Job ID
        limit: Maximum number of messages to return

    Returns:
        List of chat messages, ordered chronologically
    """
    stmt = (
        select(JobChatMessage)
        .where(JobChatMessage.job_id == job_id)
        .order_by(JobChatMessage.created_at.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def send_chat_message(
    session: AsyncSession,
    job_id: UUID,
    message: str,
    job_dir: Path,
) -> str:
    """
    Send a chat message and get LLM response via ClaudeCodeAgent.

    Args:
        session: Database session
        job_id: Job ID
        message: User's message
        job_dir: Path to job directory

    Returns:
        LLM's response text

    Raises:
        Exception: If executor call fails
    """
    # Capture filesystem state before the executor runs. The postprocessor then
    # enforces inline previews and a versioned report revision independently of
    # how the model happens to phrase its response.
    before_artifacts = _snapshot_plot_artifacts(job_dir)
    before_report = capture_report_snapshot(job_dir)
    raw_assistant_message = await _send_message_via_executor(
        session,
        job_id,
        message,
        job_dir,
    )
    figures = _changed_report_figures(job_dir, before_artifacts)
    section = update_report_markdown(job_dir, figures)

    revision = None
    report_path = job_dir / "final_report.md"
    if report_path.is_file() and before_report.markdown != report_path.read_text(
        encoding="utf-8"
    ):
        await _render_report_outputs(job_dir)
        revision = record_report_revision(
            job_dir,
            before_report,
            user_message=message,
            figures=figures,
            section=section or "Scientific Report",
        )

    assistant_message = normalize_chat_artifact_links(
        _append_chat_update_summary(
            raw_assistant_message,
            job_id,
            figures,
            revision,
        ),
        job_id,
    )

    # Store both messages in database
    user_msg = JobChatMessage(
        job_id=job_id,
        role="user",
        content=message,
    )
    session.add(user_msg)

    assistant_msg = JobChatMessage(
        job_id=job_id,
        role="assistant",
        content=assistant_message,
    )
    session.add(assistant_msg)

    await session.commit()

    return assistant_message


# Prior assistant turns can be full report dumps. Replaying them verbatim
# few-shots the model into dumping again, so cap each history message. Recent
# intent matters more than verbatim length, and the agent can re-read job files.
_HISTORY_MAX_CHARS = 800


def _truncate_history(content: str) -> str:
    """Cap a prior chat message so a long assistant report dump does not prime
    the model to repeat it."""
    content = content.strip()
    if len(content) <= _HISTORY_MAX_CHARS:
        return content
    return content[:_HISTORY_MAX_CHARS].rstrip() + " [...truncated]"


async def _send_message_via_executor(
    session: AsyncSession,
    job_id: UUID,
    message: str,
    job_dir: Path,
) -> str:
    """
    Send a chat message through the configured agent backend.

    Creates a short-lived executor via the agent factory (ClaudeCodeAgent for
    Claude-compatible providers, CodexAgent for codex-compatible ones such as
    Ollama) with the chat system prompt and full tool access, allowing the
    agent to re-analyze data or search literature when answering follow-ups.
    """
    from openscientist.agent.base import AgentConfig
    from openscientist.agent.factory import agent_class_for_provider, build_agent
    from openscientist.dvc_gateway_client import (
        DVC_CAPABILITY_ENV,
        DVC_GATEWAY_URL_ENV,
        container_dvc_gateway_base_url,
    )
    from openscientist.exec_broker_client import (
        EXEC_BROKER_URL_ENV,
        EXEC_TOKEN_ENV,
        container_broker_base_url,
    )
    from openscientist.job_container.secrets import make_dvc_capability, make_exec_placeholder
    from openscientist.providers import get_provider
    from openscientist.settings import get_settings

    # Get chat history for continuity
    history = await get_chat_history(session, job_id, limit=10)

    # System prompt is kept small (it's passed as a CLI arg to the claude
    # subprocess, so large payloads hit the OS ARG_MAX limit).  The agent
    # can read job data files on demand via Claude Code's built-in Read tool.
    assigned_skills = _load_assigned_skills(job_dir)
    system_prompt = """You are a research assistant helping a scientist discuss the results of their OpenScientist literature review and hypothesis generation job.

Your working directory is the job folder. Use available tools to inspect artifacts
and data when you need details.

Your role is to:
1. Discuss the findings from the literature review and their academic significance
2. Explain the research methodology and analysis process
3. Clarify scientific concepts mentioned in the reviewed papers
4. Help interpret the synthesized results in the context of the research question

Important: You are discussing published research and scientific literature. You are not providing personal advice — you are helping analyze what the scientific literature says.

Be concise, accurate, and cite specific papers or findings when relevant. Focus on what the research literature indicates.

When a user asks you to create or revise a plot, treat it as a scientific-report
revision: save the readable plot under `plots/`, retain provenance, and update
`final_report.md` in the most relevant existing section. Include the figure,
its caption, the interpretation that belongs with it, and any material
assumptions or limitations. If no existing section fits, use
`## Follow-up analyses from Chat`. In your reply, embed the plot with Markdown
image syntax and state the exact report section and accompanying text. The host
will enforce the preview and create the immutable report version."""
    system_prompt += _assigned_skills_prompt(assigned_skills)

    # Prompt structure matters more than wording: findings are framed as
    # background reference, long prior turns are truncated so an old report dump
    # does not few-shot a repeat, and the live user message comes LAST.
    prompt_parts = []

    job_context = await load_job_context(str(job_id))
    if job_context.strip():
        prompt_parts.append(
            "## Background reference for this job\n"
            "Context from the completed job, for you to draw on when answering. "
            "Do NOT recite or summarize it unless the user asks. Read "
            "`final_report.md` if you need full detail."
        )
        prompt_parts.append(job_context)
        prompt_parts.append("---")

    if history:
        prompt_parts.append("## Conversation so far")
        for msg in history:
            role_label = "User" if msg.role == "user" else "Assistant"
            prompt_parts.append(f"{role_label}: {_truncate_history(msg.content)}")
        prompt_parts.append("---")

    prompt_parts.append("## The user's new message (answer THIS, nothing else)")
    prompt_parts.append(
        "Respond directly to the message below. If it is not a clear question or "
        "request, reply briefly and ask what they would like to know. Do not "
        "summarize the job's findings unless the user explicitly asks for a "
        "summary."
    )
    prompt_parts.append(f"User: {message}")

    prompt = "\n".join(prompt_parts)

    logger.info(
        "Chat executor call: %d history messages, system prompt %d chars",
        len(history),
        len(system_prompt),
    )

    # Backend-specific chat prep flows through the AbstractAgent contract, not
    # isinstance: the agent class (from the provider) supplies the system prompt
    # and model override, then the executor applies its own auth and context
    # setup (no-ops on codex).
    provider = get_provider()
    agent_cls = agent_class_for_provider(provider)
    config = AgentConfig(
        job_dir=job_dir,
        system_prompt=agent_cls.chat_system_prompt(system_prompt),
        assigned_skill_ids=tuple(skill.skill_id for skill in assigned_skills),
        model_override=agent_cls.chat_model_override(),
        tool_server_env={
            # Chat's tools run in-process here, so hand them this job's exec token.
            EXEC_TOKEN_ENV: make_exec_placeholder(get_settings().secret_key, str(job_id)),
            EXEC_BROKER_URL_ENV: container_broker_base_url(),
            DVC_CAPABILITY_ENV: make_dvc_capability(
                get_settings().secret_key,
                str(job_id),
            ),
            DVC_GATEWAY_URL_ENV: container_dvc_gateway_base_url(),
        },
    )
    executor = build_agent(config, provider)
    executor.apply_runtime_environment()
    # Re-materialize the exact discovery assignment for this short-lived chat
    # agent. In particular, Codex discovers project skills only after the
    # `.agents/skills/*/SKILL.md` tree exists.
    await executor.prepare_job_workspace(use_hypotheses=False)
    executor.write_chat_context()

    try:
        result = await executor.run_iteration(prompt, reset_session=True)
        if not result.success:
            raise RuntimeError(result.error or "Chat executor returned no output")
        return result.output
    finally:
        await executor.shutdown()
