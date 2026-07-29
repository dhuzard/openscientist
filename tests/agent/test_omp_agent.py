"""Tests for `OmpAgent`.

A fake ``omp`` stub (via ``OPENSCIENTIST_OMP_BIN``) records argv and emits a
canned ``--mode=json`` stream, so no real binary or network is needed.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from openscientist.agent.base import AgentConfig, TurnOutcome
from openscientist.agent.omp_agent import OmpAgent
from openscientist.transcript import AssistantText, Reasoning, ToolCall, ToolResult, UserPrompt
from tests.helpers import StubClaudeProvider


class _Provider(StubClaudeProvider):
    """Claude-family stub with a concrete model name for arg assertions."""

    def claude_model_name(self) -> str:
        return "claude-omp-test"


# Canned stream: user, assistant+toolCall, toolResult, final assistant.
# Annotated: the entries are heterogeneous, so mypy would widen the element type
# to object and reject passing this to _write_stub.
_STREAM: list[dict[str, object]] = [
    {"type": "session", "id": "SID-abc123"},
    {"type": "agent_start"},
    {
        "type": "message_end",
        "message": {"role": "user", "content": [{"type": "text", "text": "do it"}]},
    },
    {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "model": "claude-omp-test",
            "content": [
                {"type": "thinking", "thinking": "planning", "thinkingSignature": "sig"},
                {
                    "type": "toolCall",
                    "id": "call-1",
                    "name": "bash",
                    "arguments": {"command": "echo hi"},
                },
            ],
            "usage": {"input": 10, "output": 20, "cacheRead": 5, "cacheWrite": 3},
        },
    },
    {
        "type": "message_end",
        "message": {
            "role": "toolResult",
            "toolCallId": "call-1",
            "toolName": "bash",
            "content": [{"type": "text", "text": "hi"}],
            "isError": False,
        },
    },
    {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "model": "claude-omp-test",
            "content": [{"type": "text", "text": "All done."}],
            "usage": {"input": 8, "output": 4, "cacheRead": 1, "cacheWrite": 0},
        },
    },
    {"type": "agent_end", "isTerminal": True},
]


def _write_stub(path: Path, stream: list[dict[str, object]]) -> None:
    """Write a fake-omp that records argv and emits ``stream``.

    Env knobs: ``OMP_STUB_ARGV_OUT``, ``OMP_STUB_SLEEP``, ``OMP_STUB_EXIT``,
    ``OMP_STUB_EMIT``.
    """
    payload = json.dumps(stream)
    script = f"""#!/usr/bin/env python3
import os, sys, json, time
argv_out = os.environ.get("OMP_STUB_ARGV_OUT")
if argv_out:
    with open(argv_out, "w") as fh:
        fh.write("\\n".join(sys.argv))
sleep = float(os.environ.get("OMP_STUB_SLEEP", "0"))
if sleep:
    time.sleep(sleep)
if os.environ.get("OMP_STUB_EMIT", "1") == "1":
    for event in json.loads({payload!r}):
        print(json.dumps(event))
sys.exit(int(os.environ.get("OMP_STUB_EXIT", "0")))
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _agent(tmp_path: Path, **cfg_kwargs: object) -> OmpAgent:
    config = AgentConfig(job_dir=tmp_path, **cfg_kwargs)  # type: ignore[arg-type]
    return OmpAgent(config, _Provider())


@pytest.fixture
def stub_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_path = tmp_path / "fake_omp"
    _write_stub(bin_path, _STREAM)
    monkeypatch.setenv("OPENSCIENTIST_OMP_BIN", str(bin_path))
    monkeypatch.setenv("OMP_STUB_ARGV_OUT", str(tmp_path / "argv.txt"))
    return bin_path


class TestBuildArgs:
    def test_core_flags_present(self, tmp_path: Path) -> None:
        agent = _agent(tmp_path)
        args = agent._build_args(tmp_path / "sp.md", tmp_path / "turn.md", tmp_path / "c.yml")
        assert "-p" in args
        assert "--mode=json" in args
        assert "--auto-approve" in args
        assert f"--cwd={tmp_path.resolve()}" in args
        assert f"--system-prompt={tmp_path / 'sp.md'}" in args
        assert f"--session-dir={tmp_path.resolve() / '.omp' / 'session'}" in args
        assert f"--config={tmp_path / 'c.yml'}" in args
        assert "--model=claude-omp-test" in args
        assert args[-1] == f"@{tmp_path / 'turn.md'}"

    def test_code_execution_tools_are_withheld(self, tmp_path: Path) -> None:
        """Analysis must go through execute_code, which runs in the sandboxed
        executor and captures figures, not omp's in-container code tools."""
        agent = _agent(tmp_path)
        flag = next(
            a for a in agent._build_args(tmp_path, tmp_path, tmp_path) if a.startswith("--tools=")
        )
        enabled = set(flag.removeprefix("--tools=").split(","))
        assert "write" in enabled, "write is how omp reaches MCP tools"
        assert enabled.isdisjoint({"eval", "python", "bash", "notebook"})

    def test_resume_only_when_session_known(self, tmp_path: Path) -> None:
        agent = _agent(tmp_path)
        args = agent._build_args(tmp_path, tmp_path, tmp_path)
        assert not any(a.startswith("--resume=") for a in args)
        agent._session_id = "SID-xyz"
        assert "--resume=SID-xyz" in agent._build_args(tmp_path, tmp_path, tmp_path)

    def test_model_override_wins(self, tmp_path: Path) -> None:
        agent = _agent(tmp_path, model_override="opus-override")
        assert "--model=opus-override" in agent._build_args(tmp_path, tmp_path, tmp_path)


class TestOmpConfigOverlay:
    def test_disables_xdev_so_mcp_tools_are_callable(self, tmp_path: Path) -> None:
        """With xdev on, MCP tools are xd:// devices and the shared prompts'
        plain tool names resolve to nothing."""
        import yaml

        agent = _agent(tmp_path)
        path = agent._write_omp_config()
        assert path == tmp_path.resolve() / ".omp" / "omp-config.yml"
        assert yaml.safe_load(path.read_text()) == {"tools": {"xdev": False}}


class TestMcpConfig:
    def test_writes_openscientist_tools_server(self, tmp_path: Path) -> None:
        agent = _agent(tmp_path, use_hypotheses=True)
        agent._write_mcp_config()
        cfg = json.loads((tmp_path / ".omp" / "mcp.json").read_text())
        server = cfg["mcpServers"]["openscientist-tools"]
        assert server["type"] == "stdio"
        assert server["args"] == ["-m", "openscientist_tools"]
        assert server["cwd"] == str(tmp_path.resolve())
        assert server["env"]["OPENSCIENTIST_JOB_ID"] == tmp_path.resolve().name
        assert server["env"]["OPENSCIENTIST_USE_HYPOTHESES"] == "1"

    def test_inherited_env_is_referenced_by_name_not_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The job dir is a downloadable artifact, so the config must not hold
        secret values. omp substitutes an env value whose entry names a set
        variable, so the name alone is enough to reach the tools server."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:hunter2@db:5432/os")
        monkeypatch.setenv("OPENSCIENTIST_SECRET_KEY", "s3cret-master-key")
        agent = _agent(tmp_path)
        agent._write_mcp_config()
        raw = (tmp_path / ".omp" / "mcp.json").read_text()
        env = json.loads(raw)["mcpServers"]["openscientist-tools"]["env"]

        assert env["DATABASE_URL"] == "DATABASE_URL"
        assert env["OPENSCIENTIST_SECRET_KEY"] == "OPENSCIENTIST_SECRET_KEY"
        assert "hunter2" not in raw
        assert "s3cret-master-key" not in raw


class TestRunIteration:
    @pytest.mark.asyncio
    async def test_success_parses_stream(self, tmp_path: Path, stub_bin: Path) -> None:
        agent = _agent(tmp_path, system_prompt="SYS")
        result = await agent.run_iteration("do it")

        assert result.outcome is TurnOutcome.COMPLETED
        assert result.output == "All done."
        assert result.tool_calls == 1
        # Session id captured for continuity, and passed on the next turn.
        assert agent._session_id == "SID-abc123"
        # Usage summed across the two assistant messages, additive per field.
        usage = agent.total_tokens
        assert usage.input_tokens == 18
        assert usage.output_tokens == 24
        assert usage.cache_read_tokens == 6
        assert usage.cache_write_tokens == 3
        # Transcript translated through the OMP deserializer, in order.
        types = [type(e) for e in result.transcript]
        assert types == [UserPrompt, Reasoning, ToolCall, ToolResult, AssistantText]

    @pytest.mark.asyncio
    async def test_second_turn_resumes_session(self, tmp_path: Path, stub_bin: Path) -> None:
        agent = _agent(tmp_path, system_prompt="SYS")
        await agent.run_iteration("first")
        await agent.run_iteration("second")
        argv = (tmp_path / "argv.txt").read_text().splitlines()
        assert "--resume=SID-abc123" in argv

    @pytest.mark.asyncio
    async def test_reset_session_drops_resume(self, tmp_path: Path, stub_bin: Path) -> None:
        agent = _agent(tmp_path, system_prompt="SYS")
        agent._session_id = "STALE"
        await agent.run_iteration("go", reset_session=True)
        argv = (tmp_path / "argv.txt").read_text().splitlines()
        assert not any(a == "--resume=STALE" for a in argv)

    @pytest.mark.asyncio
    async def test_nonzero_exit_no_output_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bin_path = tmp_path / "fake_omp"
        _write_stub(bin_path, _STREAM)
        monkeypatch.setenv("OPENSCIENTIST_OMP_BIN", str(bin_path))
        monkeypatch.setenv("OMP_STUB_EMIT", "0")
        monkeypatch.setenv("OMP_STUB_EXIT", "3")
        agent = _agent(tmp_path, system_prompt="SYS")
        result = await agent.run_iteration("go")
        assert result.outcome is TurnOutcome.FAILED
        assert result.error

    @pytest.mark.asyncio
    async def test_turn_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bin_path = tmp_path / "fake_omp"
        _write_stub(bin_path, _STREAM)
        monkeypatch.setenv("OPENSCIENTIST_OMP_BIN", str(bin_path))
        monkeypatch.setenv("OMP_STUB_SLEEP", "3")
        monkeypatch.setattr("openscientist.agent.omp_agent._TURN_TIMEOUT_SECONDS", 1)
        agent = _agent(tmp_path, system_prompt="SYS")
        result = await agent.run_iteration("go")
        assert result.outcome is TurnOutcome.TIMED_OUT


class TestAuthProvisioning:
    """OMP_AUTH_HOST_PATH provisioning and PI_CODING_AGENT_DIR wiring."""

    def test_provisions_store_and_points_agent_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openscientist.settings import get_settings

        src = tmp_path / "omp-store"
        src.mkdir()
        (src / "agent.db").write_text("db", encoding="utf-8")
        (src / "agent.db-wal").write_text("wal", encoding="utf-8")
        (src / "config.yml").write_text("cfg", encoding="utf-8")

        monkeypatch.setenv("OMP_AUTH_HOST_PATH", str(src))
        get_settings.cache_clear()
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        try:
            OmpAgent.provision_host_prelaunch(get_settings(), job_dir)
        finally:
            get_settings.cache_clear()

        home = job_dir / ".omp-home"
        assert (home / "agent.db").read_text() == "db"
        assert (home / "config.yml").read_text() == "cfg"

        agent = _agent(job_dir)
        assert agent._build_subprocess_env()["PI_CODING_AGENT_DIR"] == str(home)

    def test_no_provisioning_leaves_agent_dir_unset(self, tmp_path: Path) -> None:
        agent = _agent(tmp_path)
        assert "PI_CODING_AGENT_DIR" not in agent._build_subprocess_env()
