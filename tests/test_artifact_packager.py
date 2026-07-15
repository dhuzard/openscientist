"""Tests for openscientist.artifact_packager module."""

import stat
import zipfile

import pytest

from openscientist.artifact_packager import (
    EXCLUDED_FILES_MANIFEST,
    MAX_ARTIFACT_FILE_SIZE_BYTES,
    MAX_TOTAL_ARCHIVE_SIZE_BYTES,
    create_artifacts_zip,
    create_artifacts_zip_file,
)


class TestCreateArtifactsZip:
    """Tests for create_artifacts_zip()."""

    def test_creates_valid_zip(self, tmp_path):
        (tmp_path / "report.md").write_text("# Report")
        (tmp_path / "config.json").write_text('{"job_id": "j1"}')

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert "report.md" in names
            assert "config.json" not in names

    def test_excludes_git_and_pycache(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "mod.pyc").write_bytes(b"\x00")
        (tmp_path / "keep.txt").write_text("keep")

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert "keep.txt" in names
            assert not any(".git" in n for n in names)
            assert not any("__pycache__" in n for n in names)

    def test_excludes_codex_runtime_state(self, tmp_path):
        codex_home = tmp_path / ".codex"
        codex_home.mkdir()
        (codex_home / "config.toml").write_text('OPENSCIENTIST_SECRET_KEY = "placeholder"')
        (codex_home / "auth.json").write_text('{"tokens": "placeholder"}')
        (tmp_path / "report.md").write_text("# Report")

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert "report.md" in names
            assert not any(name == ".codex" or name.startswith(".codex/") for name in names)

    def test_excludes_omp_runtime_state_and_credential_vault(self, tmp_path):
        """The omp harness writes its MCP config and a copy of the credential
        vault into the job dir, so neither may reach a downloadable archive."""
        omp_dir = tmp_path / ".omp"
        omp_dir.mkdir()
        (omp_dir / "mcp.json").write_text('{"env": {"OPENSCIENTIST_EXEC_TOKEN": "job-1.tok"}}')
        (omp_dir / "session").mkdir()
        (omp_dir / "session" / "turn.jsonl").write_text("{}")
        vault = tmp_path / ".omp-home"
        vault.mkdir()
        (vault / "agent.db").write_bytes(b"SQLite format 3\x00")
        (tmp_path / "report.md").write_text("# Report")

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        assert "report.md" in names
        assert not any(n == ".omp" or n.startswith(".omp/") for n in names)
        assert not any(n == ".omp-home" or n.startswith(".omp-home/") for n in names)

    def test_no_excluded_directory_ever_reaches_the_archive(self, tmp_path):
        """Guards the denylist itself: every entry must actually be pruned, so a
        future harness cannot add a directory here and leave it unenforced."""
        from openscientist.artifact_packager import _EXCLUDE_DIRS

        for name in _EXCLUDE_DIRS:
            excluded = tmp_path / name
            excluded.mkdir()
            (excluded / "secret.txt").write_text("credential")
        (tmp_path / "report.md").write_text("# Report")

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        assert "report.md" in names
        for name in _EXCLUDE_DIRS:
            assert not any(n == name or n.startswith(f"{name}/") for n in names), name

    def test_excludes_symlinks_to_runtime_state(self, tmp_path):
        codex_home = tmp_path / ".codex"
        codex_home.mkdir()
        auth_file = codex_home / "auth.json"
        auth_file.write_text('{"tokens": "placeholder"}')
        linked_artifact = tmp_path / "result.json"
        try:
            linked_artifact.symlink_to(auth_file)
        except OSError:
            pytest.skip("symlink creation is not available on this platform")

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            assert "result.json" not in zf.namelist()

    def test_excludes_directory_symlinks_to_runtime_state(self, tmp_path):
        codex_home = tmp_path / ".codex"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text('{"tokens": "placeholder"}')
        linked_artifact_dir = tmp_path / "results"
        try:
            linked_artifact_dir.symlink_to(codex_home, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlink creation is not available on this platform")

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            assert not any(name.startswith("results/") for name in zf.namelist())

    def test_excludes_pytest_cache_and_node_modules(self, tmp_path):
        (tmp_path / ".pytest_cache").mkdir()
        (tmp_path / ".pytest_cache" / "v").write_text("data")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg").write_text("pkg")
        (tmp_path / "data.csv").write_text("a,b")

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert "data.csv" in names
            assert not any(".pytest_cache" in n for n in names)
            assert not any("node_modules" in n for n in names)

    def test_relative_paths_in_archive(self, tmp_path):
        sub = tmp_path / "data"
        sub.mkdir()
        (sub / "file.csv").write_text("x,y")
        (tmp_path / "report.md").write_text("# R")

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            # Paths should be relative to job_dir, using forward slashes
            assert "data/file.csv" in names
            assert "report.md" in names
            # No absolute paths
            assert not any(n.startswith("/") for n in names)

    def test_empty_directory(self, tmp_path):
        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            assert zf.namelist() == []

    def test_unreadable_file_skipped(self, tmp_path):
        good = tmp_path / "good.txt"
        good.write_text("good")
        bad = tmp_path / "bad.txt"
        bad.write_text("bad")
        bad.chmod(0o000)

        try:
            buf = create_artifacts_zip(tmp_path, "j1")

            with zipfile.ZipFile(buf) as zf:
                names = zf.namelist()
                assert "good.txt" in names
                # bad.txt should be skipped (logged warning), not crash
        finally:
            # Restore permissions for cleanup
            bad.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_buffer_seeked_to_zero(self, tmp_path):
        (tmp_path / "f.txt").write_text("data")
        buf = create_artifacts_zip(tmp_path, "j1")
        assert buf.tell() == 0

    def test_create_artifacts_zip_file(self, tmp_path):
        (tmp_path / "report.md").write_text("# Report")
        (tmp_path / "config.json").write_text('{"job_id":"j1"}')
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "config.toml").write_text('DATABASE_URL = "placeholder"')
        (tmp_path / ".codex" / "auth.json").write_text('{"tokens": "placeholder"}')
        archive_path = tmp_path / "artifacts.zip"

        written = create_artifacts_zip_file(tmp_path, archive_path, "j1")

        assert written == 1
        assert archive_path.exists()
        with zipfile.ZipFile(archive_path) as zf:
            names = zf.namelist()
            assert "report.md" in names
            assert "config.json" not in names
            assert not any(name == ".codex" or name.startswith(".codex/") for name in names)


class TestOversizedFileExclusion:
    """A job dir can contain arbitrarily large agent-downloaded reference
    data (e.g. a full knowledge graph), not just small user uploads --
    files over MAX_ARTIFACT_FILE_SIZE_BYTES must be excluded from the
    archive and listed in a manifest instead of ballooning it."""

    def test_oversized_file_excluded_small_file_kept(self, tmp_path):
        sub = tmp_path / "data"
        sub.mkdir()
        small = sub / "small.csv"
        small.write_text("a,b\n1,2\n")
        huge = sub / "huge_reference_data.jsonl"
        with open(huge, "wb") as f:
            f.seek(MAX_ARTIFACT_FILE_SIZE_BYTES + 1024)
            f.write(b"\0")

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert "data/small.csv" in names
            assert "data/huge_reference_data.jsonl" not in names
            assert EXCLUDED_FILES_MANIFEST in names
            manifest = zf.read(EXCLUDED_FILES_MANIFEST).decode()
            assert "data/huge_reference_data.jsonl" in manifest

    def test_no_manifest_when_nothing_excluded(self, tmp_path):
        (tmp_path / "report.md").write_text("# Report")

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            assert EXCLUDED_FILES_MANIFEST not in zf.namelist()

    def test_file_at_exactly_the_limit_is_kept(self, tmp_path):
        exact = tmp_path / "exact.bin"
        with open(exact, "wb") as f:
            f.seek(MAX_ARTIFACT_FILE_SIZE_BYTES - 1)
            f.write(b"\0")

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            assert "exact.bin" in zf.namelist()

    def test_oversized_file_excluded_from_zip_file_variant(self, tmp_path):
        huge = tmp_path / "huge.bin"
        with open(huge, "wb") as f:
            f.seek(MAX_ARTIFACT_FILE_SIZE_BYTES + 1024)
            f.write(b"\0")
        (tmp_path / "report.md").write_text("# Report")
        archive_path = tmp_path / "artifacts.zip"

        written = create_artifacts_zip_file(tmp_path, archive_path, "j1")

        assert written == 1
        with zipfile.ZipFile(archive_path) as zf:
            names = zf.namelist()
            assert "report.md" in names
            assert "huge.bin" not in names
            assert EXCLUDED_FILES_MANIFEST in names


class TestTotalArchiveSizeCap:
    """Many files individually under the per-file limit can still add up to
    an unreasonably large archive -- the combined total must also be capped,
    or a job with hundreds of medium-sized files reproduces the same
    memory/event-loop pressure a single huge file did."""

    def test_files_beyond_total_cap_are_excluded(self, tmp_path):
        # Each file is comfortably under the per-file limit (50 MB), but
        # enough of them together exceed MAX_TOTAL_ARCHIVE_SIZE_BYTES (500 MB).
        chunk = 40 * 1024 * 1024
        num_files = (MAX_TOTAL_ARCHIVE_SIZE_BYTES // chunk) + 3
        for i in range(num_files):
            f = tmp_path / f"part_{i}.bin"
            with open(f, "wb") as fh:
                fh.seek(chunk)
                fh.write(b"\0")

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            included = [n for n in names if n != EXCLUDED_FILES_MANIFEST]
            # Only as many chunks as fit under the total cap should be kept.
            assert len(included) < num_files
            assert EXCLUDED_FILES_MANIFEST in names
            manifest = zf.read(EXCLUDED_FILES_MANIFEST).decode()
            assert "archive size limit reached" in manifest


class TestManifestNameCollision:
    """A job dir could legitimately contain a real file literally named
    EXCLUDED_FILES.txt (e.g. the agent wrote one) -- it must not collide
    with the manifest entry added when other files are excluded."""

    def test_preexisting_file_with_manifest_name_is_not_bundled(self, tmp_path):
        (tmp_path / EXCLUDED_FILES_MANIFEST).write_text("not the real manifest")
        huge = tmp_path / "huge.bin"
        with open(huge, "wb") as f:
            f.seek(MAX_ARTIFACT_FILE_SIZE_BYTES + 1024)
            f.write(b"\0")

        buf = create_artifacts_zip(tmp_path, "j1")

        with zipfile.ZipFile(buf) as zf:
            # Exactly one EXCLUDED_FILES.txt entry -- the generated manifest,
            # not a duplicate zip entry from the pre-existing file.
            assert zf.namelist().count(EXCLUDED_FILES_MANIFEST) == 1
            manifest = zf.read(EXCLUDED_FILES_MANIFEST).decode()
            assert "huge.bin" in manifest
            assert "not the real manifest" not in manifest
