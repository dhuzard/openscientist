"""Tests for the reproducible local quality command runner."""

from __future__ import annotations

from dataclasses import dataclass

from openscientist import quality


@dataclass
class FakeDockerClient:
    available: bool = True
    closed: bool = False

    def ping(self) -> bool:
        return self.available

    def close(self) -> None:
        self.closed = True


def test_docker_preflight_accepts_available_daemon(capsys) -> None:
    client = FakeDockerClient()

    assert quality.require_docker(lambda: client) == 0
    assert client.closed is True
    assert "Docker daemon is available" in capsys.readouterr().out


def test_docker_preflight_reports_explicit_blocker(capsys) -> None:
    def unavailable() -> FakeDockerClient:
        raise RuntimeError("daemon unavailable")

    assert quality.require_docker(unavailable) == quality.BLOCKED_EXIT_CODE
    captured = capsys.readouterr()
    assert "BLOCKED: Docker daemon is unavailable" in captured.err
    assert "make quality-integration" in captured.err


def test_docker_preflight_closes_client_after_failed_ping() -> None:
    client = FakeDockerClient(available=False)

    assert quality.require_docker(lambda: client) == quality.BLOCKED_EXIT_CODE
    assert client.closed is True


def test_windows_integration_disables_unreliable_ryuk_sidecar(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def run_commands(label, commands, *, env=None):
        assert label == "quality-integration"
        assert commands
        captured.update(env or {})
        return 0

    monkeypatch.setattr(quality, "require_docker", lambda: 0)
    monkeypatch.setattr(quality, "_run_commands", run_commands)
    monkeypatch.setattr(quality.sys, "platform", "win32")

    assert quality.run_integration() == 0
    assert captured["TESTCONTAINERS_RYUK_DISABLED"] == "true"
