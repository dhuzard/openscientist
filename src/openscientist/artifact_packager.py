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

logger = logging.getLogger(__name__)

EXCLUDED_FILES_MANIFEST = "EXCLUDED_FILES.txt"

# Agent working directories are never artifacts, and they hold credentials: the
# per-job MCP config, session transcripts, and the copied omp credential vault.
_EXCLUDE_DIRS = {
    ".codex",
    ".omp",
    ".omp-home",
    ".git",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
}
# EXCLUDED_FILES_MANIFEST is excluded from the *input* walk so a same-named
# file already present in job_dir can't collide with the manifest entry
# _write_artifacts_zip adds afterward.
_EXCLUDE_FILES = {"config.json", EXCLUDED_FILES_MANIFEST}

# Job directories can contain arbitrarily large reference data the agent
# downloaded as analysis input (e.g. a full knowledge graph), not just small
# user-uploaded files. Files over this size are left out of the artifacts
# ZIP and noted in EXCLUDED_FILES_MANIFEST instead of silently ballooning
# the archive to tens of GB.
MAX_ARTIFACT_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


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


def _partition_by_size(
    files: Iterator[tuple[Path, Path]],
) -> tuple[list[tuple[Path, Path]], list[tuple[Path, int]]]:
    """Split (path, arcname) pairs into (includable, oversized) by file size.

    oversized entries are (arcname, size_bytes), for manifest reporting.
    """
    included: list[tuple[Path, Path]] = []
    oversized: list[tuple[Path, int]] = []
    for file_path, arcname in files:
        try:
            size = file_path.stat().st_size
        except OSError:
            included.append((file_path, arcname))
            continue
        if size > MAX_ARTIFACT_FILE_SIZE_BYTES:
            oversized.append((arcname, size))
        else:
            included.append((file_path, arcname))
    return included, oversized


def _format_excluded_manifest(oversized: list[tuple[Path, int]]) -> str:
    lines = [
        "The following files were left out of this archive because they exceed "
        f"{MAX_ARTIFACT_FILE_SIZE_BYTES // (1024 * 1024)} MB. Large files here are "
        "often reference datasets used as analysis input rather than report "
        "outputs; check the job's data sources if you need one of these.",
        "",
    ]
    for arcname, size in sorted(oversized, key=lambda pair: pair[1], reverse=True):
        lines.append(f"{size / (1024 * 1024):>10.1f} MB  {arcname.as_posix()}")
    return "\n".join(lines) + "\n"


def _write_artifacts_zip(
    zip_file: zipfile.ZipFile,
    job_dir: Path,
    excluded_paths: set[Path] | None = None,
) -> int:
    """Write job artifacts into an open zip file and return number of files written."""
    included, oversized = _partition_by_size(
        _iter_artifact_files(job_dir, excluded_paths=excluded_paths)
    )

    written = 0
    for file_path, arcname in included:
        try:
            zip_file.write(file_path, arcname)
            written += 1
        except Exception as e:
            logger.warning("Failed to add %s to archive: %s", arcname, e)

    if oversized:
        zip_file.writestr(EXCLUDED_FILES_MANIFEST, _format_excluded_manifest(oversized))
        logger.info(
            "Excluded %d oversized file(s) from artifacts archive (over %d MB): %s",
            len(oversized),
            MAX_ARTIFACT_FILE_SIZE_BYTES // (1024 * 1024),
            ", ".join(arcname.as_posix() for arcname, _ in oversized),
        )

    return written


def create_artifacts_zip(job_dir: Path, job_id: str) -> BytesIO:
    """
    Create a ZIP archive of all job artifacts.

    Includes:
    - Final reports (PDF, Markdown)
    - Plots and visualizations
    - Data files (up to MAX_ARTIFACT_FILE_SIZE_BYTES each)
    - Provenance logs

    Files over MAX_ARTIFACT_FILE_SIZE_BYTES (e.g. large reference datasets
    downloaded as analysis input) are excluded and listed in
    EXCLUDED_FILES_MANIFEST instead of being bundled.

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
    """Create an artifacts ZIP archive on disk and return number of files written.

    See create_artifacts_zip() for the oversized-file exclusion behavior.
    """
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
