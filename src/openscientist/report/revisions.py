"""Versioned scientific-report updates created from completed-job chat.

The live ``final_report.*`` files always represent the latest report. Immutable
snapshots below ``report_versions/`` preserve the original report and each
subsequent chat-authored revision, while a small manifest provides provenance
for the UI and downstream exports.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPORT_FILENAMES = ("final_report.md", "final_report.html", "final_report.pdf")
_MANIFEST_RELATIVE_PATH = Path("provenance") / "report_versions.json"
_VERSIONS_DIRNAME = "report_versions"
_CHAT_SECTION = "Follow-up analyses from Chat"


@dataclass(frozen=True)
class ReportSnapshot:
    """In-memory report state captured before a chat turn runs."""

    files: dict[str, bytes]

    @property
    def markdown(self) -> str | None:
        payload = self.files.get("final_report.md")
        return payload.decode("utf-8") if payload is not None else None


@dataclass(frozen=True)
class ReportFigure:
    """A plot created or modified during a chat turn."""

    relative_path: str
    title: str
    caption: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportRevision:
    """The report placement and version recorded for a chat update."""

    version: int
    section: str
    figures: tuple[ReportFigure, ...]


def capture_report_snapshot(job_dir: Path) -> ReportSnapshot:
    """Capture current report outputs before the agent can modify them."""
    files = {
        name: path.read_bytes() for name in _REPORT_FILENAMES if (path := job_dir / name).is_file()
    }
    return ReportSnapshot(files=files)


def load_report_version_manifest(job_dir: Path) -> dict[str, Any] | None:
    """Load a valid report-version manifest, returning ``None`` if absent."""
    manifest_path = job_dir / _MANIFEST_RELATIVE_PATH
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("versions"), list):
        return None
    return payload


def _write_manifest(job_dir: Path, manifest: dict[str, Any]) -> None:
    manifest_path = job_dir / _MANIFEST_RELATIVE_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _version_dir(job_dir: Path, version: int) -> Path:
    return job_dir / _VERSIONS_DIRNAME / f"v{version}"


def _copy_snapshot(snapshot: ReportSnapshot, destination: Path) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name, payload in snapshot.files.items():
        (destination / name).write_bytes(payload)
        copied.append(name)
    return copied


def _copy_live_report(job_dir: Path, destination: Path) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in _REPORT_FILENAMES:
        source = job_dir / name
        if source.is_file():
            shutil.copy2(source, destination / name)
            copied.append(name)
    return copied


def _copy_revision_artifacts(
    job_dir: Path,
    destination: Path,
    figures: tuple[ReportFigure, ...],
) -> list[str]:
    copied: list[str] = []
    for figure in figures:
        source = job_dir / figure.relative_path
        if not source.is_file():
            continue
        target = destination / "artifacts" / figure.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append((Path("artifacts") / figure.relative_path).as_posix())
    return copied


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_baseline(job_dir: Path, before: ReportSnapshot) -> dict[str, Any]:
    manifest = load_report_version_manifest(job_dir)
    if manifest is not None:
        return manifest

    files = _copy_snapshot(before, _version_dir(job_dir, 1))
    manifest = {
        "schema_version": 1,
        "current_version": 1,
        "versions": [
            {
                "version": 1,
                "created_at": _utc_now(),
                "source": "job-completion",
                "summary": "Original completed scientific report",
                "section": None,
                "accompanying_text": None,
                "figures": [],
                "files": files,
            }
        ],
    }
    _write_manifest(job_dir, manifest)
    return manifest


def _sanitize_figure_parameter(text: str) -> str:
    return " ".join(text.replace("|", "—").replace("}", ")").split())


def _report_references_figure(markdown: str, figure: ReportFigure) -> bool:
    references = (figure.relative_path, *figure.aliases)
    return any(
        reference in markdown or Path(reference).name in markdown for reference in references
    )


def _last_heading_before(markdown: str, needle: str) -> str | None:
    index = markdown.find(needle)
    if index < 0:
        index = markdown.find(Path(needle).name)
    if index < 0:
        return None
    headings = [
        line.lstrip("#").strip() for line in markdown[:index].splitlines() if line.startswith("#")
    ]
    return headings[-1] if headings else None


def update_report_markdown(
    job_dir: Path,
    figures: tuple[ReportFigure, ...],
) -> str | None:
    """Ensure generated figures occur in the live report and return their section."""
    report_path = job_dir / "final_report.md"
    if not report_path.is_file() or not figures:
        return None

    markdown = report_path.read_text(encoding="utf-8")
    referenced = [figure for figure in figures if _report_references_figure(markdown, figure)]
    missing = [figure for figure in figures if figure not in referenced]

    if missing:
        chunks: list[str] = []
        if f"## {_CHAT_SECTION}" not in markdown:
            chunks.append(f"## {_CHAT_SECTION}")
        for figure in missing:
            caption = _sanitize_figure_parameter(figure.caption)
            chunks.extend(
                [
                    f"### {figure.title}",
                    figure.caption,
                    (f"{{{{figure:{figure.relative_path}|caption={caption}|width=100%}}}}"),
                ]
            )
        markdown = markdown.rstrip() + "\n\n" + "\n\n".join(chunks) + "\n"
        report_path.write_text(markdown, encoding="utf-8")
        return _CHAT_SECTION

    sections: set[str] = set()
    for figure in referenced:
        for reference in (figure.relative_path, *figure.aliases):
            heading = _last_heading_before(markdown, reference)
            if heading is not None:
                sections.add(heading)
                break
    return next(iter(sections)) if len(sections) == 1 else "Existing scientific report sections"


def record_report_revision(
    job_dir: Path,
    before: ReportSnapshot,
    *,
    user_message: str,
    figures: tuple[ReportFigure, ...],
    section: str,
) -> ReportRevision | None:
    """Record a new immutable revision when the live Markdown changed."""
    report_path = job_dir / "final_report.md"
    if not report_path.is_file():
        return None
    current_markdown = report_path.read_text(encoding="utf-8")
    if before.markdown == current_markdown:
        return None

    manifest = _ensure_baseline(job_dir, before)
    version = (
        max(
            (int(item.get("version", 0)) for item in manifest["versions"]),
            default=0,
        )
        + 1
    )
    version_dir = _version_dir(job_dir, version)
    files = _copy_live_report(job_dir, version_dir)
    artifact_files = _copy_revision_artifacts(job_dir, version_dir, figures)
    accompanying_text = "\n\n".join(figure.caption for figure in figures) or None
    manifest["current_version"] = version
    manifest["versions"].append(
        {
            "version": version,
            "created_at": _utc_now(),
            "source": "job-chat",
            "summary": user_message.strip(),
            "section": section,
            "accompanying_text": accompanying_text,
            "figures": [figure.relative_path for figure in figures],
            "files": files + artifact_files,
        }
    )
    _write_manifest(job_dir, manifest)
    return ReportRevision(version=version, section=section, figures=figures)
