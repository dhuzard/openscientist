"""Tests for openscientist.artifact_packager module."""

import stat
import zipfile

import pytest

from openscientist.artifact_packager import create_artifacts_zip, create_artifacts_zip_file


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
