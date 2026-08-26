"""
Artifact packager for OpenScientist jobs.

Provides utilities for packaging job artifacts (reports, plots, logs, data)
into downloadable archives in various formats (ZIP, Markdown, JSON).
"""

import json
import logging
import zipfile
from collections.abc import Iterator
from dataclasses import asdict
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from openscientist.assays import AssayAdapter, get_assay_registry

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
_EXCLUDE_FILES = {".dvc_workflow.lock", "config.json", EXCLUDED_FILES_MANIFEST}

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


def _assay_evidence_files(
    job_dir: Path,
    adapter: AssayAdapter,
    excluded_paths: set[Path] | None = None,
) -> tuple[list[tuple[Path, Path, tuple[str, ...], tuple[str, ...]]], list[str]]:
    """Resolve adapter-declared evidence without following job-controlled links."""

    excluded_paths = excluded_paths or set()
    job_root = job_dir.resolve()
    matched: dict[Path, tuple[set[str], set[str]]] = {}
    missing_required: list[str] = []
    for evidence_pattern in adapter.evidence_patterns:
        pattern_matches = 0
        for candidate in job_dir.glob(evidence_pattern.glob):
            if not candidate.is_file() or candidate.is_symlink():
                continue
            resolved = candidate.resolve()
            if job_root not in resolved.parents or resolved in excluded_paths:
                continue
            if candidate.name in _EXCLUDE_FILES or any(
                part in _EXCLUDE_DIRS for part in candidate.relative_to(job_dir).parts
            ):
                continue
            pattern_matches += 1
            roles, schemas = matched.setdefault(resolved, (set(), set()))
            roles.add(evidence_pattern.role)
            if evidence_pattern.schema_id:
                schemas.add(evidence_pattern.schema_id)
        if evidence_pattern.required and pattern_matches == 0:
            missing_required.append(evidence_pattern.glob)

    files = [
        (
            absolute_path,
            absolute_path.relative_to(job_root),
            tuple(sorted(roles)),
            tuple(sorted(schemas)),
        )
        for absolute_path, (roles, schemas) in sorted(
            matched.items(), key=lambda item: str(item[0])
        )
    ]
    return files, sorted(missing_required)


def create_assay_evidence_bundle_zip(
    job_dir: Path,
    job_id: str,
    assay_id: str,
    *,
    manifest_name: str = "ASSAY_EVIDENCE_MANIFEST.json",
    manifest_schema: str = "openscientist-assay-evidence-bundle/1.0",
) -> BytesIO:
    """Create a contract-derived, checksum-verifiable assay evidence bundle."""

    adapter = get_assay_registry().require(assay_id)
    evidence_files, missing_required = _assay_evidence_files(job_dir, adapter)
    zip_buffer = BytesIO()
    manifest_entries: list[dict[str, Any]] = []

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path, arcname, roles, schemas in evidence_files:
            sha256, size = _write_evidence_file(zip_file, file_path, arcname)
            manifest_entries.append(
                {
                    "path": str(arcname).replace("\\", "/"),
                    "sha256": sha256,
                    "bytes": size,
                    "roles": list(roles),
                    "schema_ids": list(schemas),
                }
            )

        manifest_payload = {
            "schema": manifest_schema,
            "job_id": job_id,
            "assay_id": adapter.adapter_id,
            "adapter_version": adapter.adapter_version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "complete": not missing_required,
            "missing_required_patterns": missing_required,
            "evidence_contract": [asdict(pattern) for pattern in adapter.evidence_patterns],
            "manifest_schemas": list(adapter.manifest_schemas),
            "total_files": len(manifest_entries),
            "total_bytes": sum(item["bytes"] for item in manifest_entries),
            "files": sorted(manifest_entries, key=lambda item: item["path"]),
        }
        zip_file.writestr(
            manifest_name,
            json.dumps(manifest_payload, indent=2, sort_keys=True),
        )

    zip_buffer.seek(0)
    logger.info(
        "Created %s evidence bundle ZIP for job %s (%d files, %d bytes)",
        assay_id,
        job_id,
        len(manifest_entries),
        zip_buffer.getbuffer().nbytes,
    )
    return zip_buffer


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
    return create_assay_evidence_bundle_zip(
        job_dir,
        job_id,
        "dvc",
        manifest_name="DVC_EVIDENCE_MANIFEST.json",
        manifest_schema="openscientist-dvc-evidence-bundle/0.1",
    )
