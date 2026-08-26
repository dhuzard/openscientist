"""
Artifact packager for OpenScientist jobs.

Provides utilities for packaging job artifacts (reports, plots, logs, data)
into downloadable archives in various formats (ZIP, Markdown, JSON).
"""

import logging
import zipfile
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EXCLUDE_DIRS = {".codex", ".git", "__pycache__", ".pytest_cache", "node_modules"}
_EXCLUDE_FILES = {".dvc_workflow.lock", "config.json"}


def _iter_artifact_files(
    job_dir: Path,
    excluded_paths: set[Path] | None = None,
) -> Iterator[tuple[Path, Path]]:
    """Yield (absolute_path, archive_relative_path) pairs for artifact files."""
    excluded_paths = excluded_paths or set()
    for root, dirnames, filenames in job_dir.walk(top_down=True, follow_symlinks=False):
        # Prune before descent so linked directories are never traversed.
        safe_dirnames: list[str] = []
        for dirname in dirnames:
            dir_path = root / dirname
            if dirname in _EXCLUDE_DIRS:
                continue
            if dir_path.is_symlink():
                continue
            if dir_path.resolve() in excluded_paths:
                continue
            safe_dirnames.append(dirname)
        dirnames[:] = safe_dirnames

        for filename in filenames:
            file_path = root / filename
            # Job directories are agent-writable. Do not let file links smuggle
            # runtime credentials or files outside the job directory into an export.
            if file_path.is_symlink():
                continue
            if file_path.resolve() in excluded_paths:
                continue
            if filename in _EXCLUDE_FILES:
                continue
            yield file_path, file_path.relative_to(job_dir)


def _write_artifacts_zip(
    zip_file: zipfile.ZipFile,
    job_dir: Path,
    excluded_paths: set[Path] | None = None,
) -> int:
    """Write job artifacts into an open zip file and return number of files written."""
    written = 0
    for file_path, arcname in _iter_artifact_files(job_dir, excluded_paths=excluded_paths):
        try:
            zip_file.write(file_path, arcname)
            written += 1
        except Exception as e:
            logger.warning("Failed to add %s to archive: %s", arcname, e)
    return written


def create_artifacts_zip(job_dir: Path, job_id: str) -> BytesIO:
    """
    Create a ZIP archive of all job artifacts.

    Includes:
    - Final reports (PDF, Markdown)
    - Plots and visualizations
    - Data files
    - Provenance logs

    Args:
        job_dir: Path to job directory
        job_id: Job ID (for logging)

    Returns:
        BytesIO buffer containing ZIP archive
    """
    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        written = _write_artifacts_zip(zip_file, job_dir)

    zip_buffer.seek(0)
    logger.info(
        "Created artifacts ZIP for job %s (%d files, %d bytes)",
        job_id,
        written,
        zip_buffer.getbuffer().nbytes,
    )

    return zip_buffer


def create_artifacts_zip_file(job_dir: Path, archive_path: Path, job_id: str) -> int:
    """Create an artifacts ZIP archive on disk and return number of files written."""
    excluded_paths: set[Path] = set()
    archive_path_resolved = archive_path.resolve()
    if archive_path_resolved.is_relative_to(job_dir.resolve()):
        excluded_paths.add(archive_path_resolved)

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        written = _write_artifacts_zip(zip_file, job_dir, excluded_paths=excluded_paths)
    logger.info(
        "Created artifacts ZIP file for job %s at %s (%d files)",
        job_id,
        archive_path,
        written,
    )
    return written


def _write_evidence_file(
    zip_file: zipfile.ZipFile,
    file_path: Path,
    arcname: Path,
) -> tuple[str, int]:
    """Stream one file once so the manifest hash matches the archived bytes."""

    import hashlib

    digest = hashlib.sha256()
    size = 0
    archive_name = str(arcname).replace("\\", "/")
    with file_path.open("rb") as source, zip_file.open(archive_name, "w") as destination:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            destination.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


_DVC_BUNDLE_PATTERNS = (
    "dvc_datasets",
    "dvc_assessments",
    "dvc_approvals",
    "dvc_analyses",
    "dvc_bundles",
    "dvc_workflow.json",
    "plots",
    "provenance",
    "final_report.md",
    "final_report.html",
    "final_report.pdf",
    "EVIDENCE_PLAN.md",
    ".openscientist",
    "DVC_REAL_VALIDATION_REPORT.md",
    "dvc_validation_manifest.json",
    "dvc_udwa_parity.json",
)


def _iter_dvc_evidence_files(
    job_dir: Path,
    excluded_paths: set[Path] | None = None,
) -> Iterator[tuple[Path, Path]]:
    """Yield (absolute_path, archive_relative_path) pairs for governed DVC evidence."""
    excluded_paths = excluded_paths or set()
    for pattern in _DVC_BUNDLE_PATTERNS:
        target = job_dir / pattern
        if not target.exists():
            continue
        if target.is_file():
            if target.is_symlink() or target.resolve() in excluded_paths:
                continue
            yield target, target.relative_to(job_dir)
        elif target.is_dir():
            for root, dirnames, filenames in target.walk(top_down=True, follow_symlinks=False):
                safe_dirnames = []
                for dirname in dirnames:
                    dir_path = root / dirname
                    if (
                        dirname in _EXCLUDE_DIRS
                        or dir_path.is_symlink()
                        or dir_path.resolve() in excluded_paths
                    ):
                        continue
                    safe_dirnames.append(dirname)
                dirnames[:] = safe_dirnames

                for filename in filenames:
                    file_path = root / filename
                    if file_path.is_symlink() or file_path.resolve() in excluded_paths:
                        continue
                    if filename in _EXCLUDE_FILES:
                        continue
                    yield file_path, file_path.relative_to(job_dir)


def create_dvc_evidence_bundle_zip(job_dir: Path, job_id: str) -> BytesIO:
    """
    Create a standalone, audit-ready ZIP archive of all governed DVC evidence.

    Includes:
    - Raw DVC dataset files and manifests (dvc_datasets/)
    - Pre/Post FAIR, PREPARE, ARRIVE, and MNMS assessment checkpoints (dvc_assessments/)
    - Signed cryptographic approval records (dvc_approvals/)
    - Governed UDWA analysis results and immutable provenance (dvc_analyses/)
    - Post-analysis bundle exports (dvc_bundles/)
    - Visualizations and figures (plots/)
    - Provenance traces (provenance/)
    - Final scientific synthesis reports (final_report.md, final_report.html, final_report.pdf)
    - Auto-generated DVC_EVIDENCE_MANIFEST.json with SHA-256 checksums of all bundled assets
    """
    import json
    from datetime import datetime, timezone

    zip_buffer = BytesIO()
    manifest_entries: list[dict[str, Any]] = []

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path, arcname in _iter_dvc_evidence_files(job_dir):
            sha256, size = _write_evidence_file(zip_file, file_path, arcname)
            manifest_entries.append(
                {
                    "path": str(arcname).replace("\\", "/"),
                    "sha256": sha256,
                    "bytes": size,
                }
            )

        # Generate cryptographic manifest
        manifest_payload = {
            "schema": "openscientist-dvc-evidence-bundle/0.1",
            "job_id": job_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_files": len(manifest_entries),
            "total_bytes": sum(item["bytes"] for item in manifest_entries),
            "files": sorted(manifest_entries, key=lambda x: x["path"]),
        }
        zip_file.writestr(
            "DVC_EVIDENCE_MANIFEST.json",
            json.dumps(manifest_payload, indent=2, sort_keys=True),
        )

    zip_buffer.seek(0)
    logger.info(
        "Created DVC evidence bundle ZIP for job %s (%d files, %d bytes)",
        job_id,
        len(manifest_entries),
        zip_buffer.getbuffer().nbytes,
    )
    return zip_buffer
