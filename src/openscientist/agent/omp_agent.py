"""Oh My Pi (omp) agent backend.

``OmpAgent`` drives omp as a subprocess (``omp -p --mode=json`` per turn, parsing
the JSON-lines stream). Provider-agnostic. Per-job config lives in the job dir omp
treats as its root: a ``--system-prompt`` file, the tools MCP server in
``.omp/mcp.json``, and skills in ``.omp/skills/``. Binary from
``OPENSCIENTIST_OMP_BIN`` or ``omp`` on ``PATH``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import yaml

from openscientist.agent.base import (
    AbstractAgent,
    AgentBackend,
    AgentConfig,
    IterationResult,
    TokenUsage,
    TurnOutcome,
)
from openscientist.providers.base import LLM_PROXY_URL_ENV, Provider
from openscientist.settings import get_settings
from openscientist.transcript import OMP, TranscriptEntry

if TYPE_CHECKING:
    from openscientist.prompts.common import BackendFragments
    from openscientist.settings import Settings

logger = logging.getLogger(__name__)

_MCP_SERVER_NAME = "openscientist-tools"

# Wall-clock bound on one turn; a stuck turn is cut and the loop advances.
_TURN_TIMEOUT_SECONDS = int(os.environ.get("OPENSCIENTIST_OMP_TURN_TIMEOUT", "900"))


def _resolve_omp_bin() -> str:
    """``OPENSCIENTIST_OMP_BIN``, else ``omp`` on ``PATH`` (literal fallback)."""
    override = os.environ.get("OPENSCIENTIST_OMP_BIN")
    if override:
        return override
    return shutil.which("omp") or "omp"


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


class OmpAgent(AbstractAgent[Provider]):
    """Agent that drives the omp harness CLI over ``omp -p --mode=json``."""

    backend = AgentBackend.OMP
    file_write_tool = "write"
    display_name = "Oh My Pi"
    # omp discovers ``.omp/skills/<name>/SKILL.md`` under its cwd (default layout).
    skills_subdir = ".omp/skills"

    def __init__(self, config: AgentConfig, provider: Provider) -> None:
        super().__init__(config, provider)
        self._model_override = config.model_override
        self._session_id: str | None = None

    @classmethod
    def prompt_fragments(cls) -> BackendFragments:
        from openscientist.prompts.omp import OMP_FRAGMENTS

        return OMP_FRAGMENTS

    @classmethod
    def discovery_system_prompt(
        cls, *, use_hypotheses: bool = False, phenix_available: bool = False
    ) -> str:
        # omp takes one system prompt, so (like codex) it gets the full job doc.
        return cls.job_doc(use_hypotheses=use_hypotheses, phenix_available=phenix_available)

    async def prepare_job_workspace(self, *, use_hypotheses: bool = False) -> None:
        await super().prepare_job_workspace(use_hypotheses=use_hypotheses)
        self._write_mcp_config()

    # apply_runtime_environment/chat_*/write_chat_context use the AbstractAgent
    # defaults: omp reads auth from the subprocess env and its model from
    # config.model_override, and folds chat guidance into the system prompt.

    # Host omp store files copied to rebuild the vault in the job workspace
    # (WAL/SHM included so an un-checkpointed db stays readable).
    _OMP_STORE_FILES: ClassVar[tuple[str, ...]] = (
        "agent.db",
        "agent.db-wal",
        "agent.db-shm",
        "config.yml",
    )

    @classmethod
    def provision_host_prelaunch(cls, settings: Settings, job_dir: Path) -> None:
        """Copy the host omp credential vault into the job workspace, agent-writable
        (mounting fails on permissions for the non-root agent), and point
        ``PI_CODING_AGENT_DIR`` at the copy. No-op unless ``omp_auth_host_path``
        is set (the API-key path needs no vault)."""
        src = settings.provider.omp_auth_host_path
        if not src:
            return
        src_path = Path(src).expanduser()
        if not src_path.is_dir():
            logger.warning("omp_auth_host_path %s is not a directory, skipping", src_path)
            return
        dest = job_dir / ".omp-home"
        dest.mkdir(parents=True, exist_ok=True)
        dest.chmod(0o777)
        copied = 0
        for name in cls._OMP_STORE_FILES:
            f = src_path / name
            if f.exists():
                target = dest / name
                shutil.copy2(f, target)
                # Agent opens the SQLite vault read-write, so it must be writable.
                target.chmod(0o666)
                copied += 1
        logger.info("Provisioned omp auth (%d files) into %s", copied, dest)

    def _omp_home(self) -> Path:
        return self._job_dir() / ".omp-home"

    def _job_dir(self) -> Path:
        # Absolute: omp resolves a relative --cwd against its own launch cwd.
        return self._config.job_dir.resolve()

    def _omp_dir(self) -> Path:
        return self._job_dir() / ".omp"

    def _model_name(self) -> str | None:
        return self._model_override or self._provider.effective_model_name()

    def _mcp_env(self) -> dict[str, str]:
        """Env table for the tools MCP server, written into ``.omp/mcp.json``.

        Inherited keys are passed as variable *names*, not values. When an omp
        stdio ``env`` value names a set environment variable, omp substitutes
        that variable's value just before launching the server, so the config on
        disk never holds the secret. This matters because the job directory is a
        downloadable artifact and the tools server legitimately needs
        ``DATABASE_URL`` and the exec-broker token.

        The per-job overlay is written literally, because those values are
        computed rather than inherited and a name reference could not resolve
        them. The chat path threads a per-job exec token through that overlay,
        so this alone is not sufficient: ``.omp`` is also excluded from packaged
        artifacts in ``artifact_packager``.
        """
        env = {name: name for name in os.environ}
        env.update(self._job_env_overlay(self._job_dir()))
        return env

    def _write_mcp_config(self) -> None:
        """Write ``.omp/mcp.json`` wiring the ``openscientist-tools`` server."""
        omp_dir = self._omp_dir()
        omp_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "mcpServers": {
                _MCP_SERVER_NAME: {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": ["-m", "openscientist_tools"],
                    "cwd": str(self._job_dir()),
                    "env": self._mcp_env(),
                }
            }
        }
        (omp_dir / "mcp.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    def _write_omp_config(self) -> Path:
        """Write the per-run omp config overlay and return its path.

        ``tools.xdev`` defaults on, which mounts MCP tools as ``xd://`` devices
        driven through ``write`` instead of exposing them as callable tools. The
        shared prompts name the openscientist tools plainly (as Claude and codex
        see them), so disabling it keeps one tool vocabulary across backends
        rather than teaching omp a second calling convention. The cost is that
        every enabled tool's schema ships on each request, which is why
        ``_OMP_ENABLED_TOOLS`` is a short list.
        """
        omp_dir = self._omp_dir()
        omp_dir.mkdir(parents=True, exist_ok=True)
        path = omp_dir / "omp-config.yml"
        path.write_text(yaml.safe_dump({"tools": {"xdev": False}}), encoding="utf-8")
        return path

    def _write_system_prompt(self) -> Path:
        omp_dir = self._omp_dir()
        omp_dir.mkdir(parents=True, exist_ok=True)
        path = omp_dir / "system_prompt.md"
        path.write_text(self._config.system_prompt or "", encoding="utf-8")
        return path

    def _write_turn_prompt(self, prompt: str) -> Path:
        # Passed as ``@<path>`` so a large prompt never hits the argv limit.
        omp_dir = self._omp_dir()
        omp_dir.mkdir(parents=True, exist_ok=True)
        path = omp_dir / "turn_prompt.md"
        path.write_text(prompt, encoding="utf-8")
        return path

    def _write_omp_model_catalog(self) -> None:
        """Write the active provider's ``models.yml`` into the omp home, if any."""
        catalog = self._provider.omp_model_catalog()
        if not catalog:
            return
        home = self._omp_home()
        if not home.exists():
            # Only when we create it: the vault provisioner already made it
            # agent-writable, and chmod on a root-owned dir fails for the agent.
            home.mkdir(parents=True, exist_ok=True)
            home.chmod(0o777)
        path = home / "models.yml"
        path.write_text(yaml.safe_dump(dict(catalog), sort_keys=False), encoding="utf-8")
        logger.info("Wrote omp model catalog to %s", path)

    def _build_subprocess_env(self) -> dict[str, str]:
        """omp process env: inherited env plus provider auth, the per-job overlay,
        and the proxy base URL when active. The container env is overlaid so auth
        works in both the runner (os.environ pre-injected) and the web/chat process
        (not). omp reads ``ANTHROPIC_BASE_URL`` natively, OpenAI-family providers
        get the proxy as ``OPENAI_BASE_URL``."""
        provider_settings = get_settings().provider
        env = dict(os.environ)
        env.update(provider_settings.get_container_env_vars())
        env.update(self._job_env_overlay(self._job_dir()))

        # Declare a self-hosted model to omp. Its built-in catalog knows hosted
        # APIs only, so without this omp cannot resolve --model and fails with
        # "Model ... not found" before ever reaching the server.
        self._write_omp_model_catalog()

        # Use the provisioned vault (e.g. ChatGPT subscription) as omp's home.
        omp_home = self._omp_home()
        if omp_home.is_dir():
            env["PI_CODING_AGENT_DIR"] = str(omp_home)

        # Provider-specific base-URL env for a generic harness (proxy or local).
        for key, value in self._provider.harness_env(proxy=env.get(LLM_PROXY_URL_ENV)).items():
            env.setdefault(key, value)
        return env

    #: omp built-in tools the discovery loop may use. ``--tools`` is an enable
    #: list, so everything absent here is off, notably omp's own code execution.
    #: Analysis MUST go through the ``execute_code`` MCP tool: it runs in the
    #: sandboxed executor container and captures figures into the report, whereas
    #: omp's ``eval`` runs inside the agent container and only renders figures
    #: inline, so its plots never reach the job artifacts. ``write`` is required:
    #: omp invokes MCP tools by writing JSON to their ``xd://`` device.
    _OMP_ENABLED_TOOLS: ClassVar[tuple[str, ...]] = (
        "read",
        "write",
        "edit",
        "grep",
        "glob",
        "todo",
    )

    def _build_args(
        self, system_prompt_path: Path, prompt_path: Path, config_path: Path
    ) -> list[str]:
        job_dir = self._job_dir()
        args = [
            _resolve_omp_bin(),
            "-p",
            "--mode=json",
            "--no-title",
            "--no-lsp",
            "--no-pty",
            "--auto-approve",
            f"--config={config_path}",
            f"--tools={','.join(self._OMP_ENABLED_TOOLS)}",
            f"--cwd={job_dir}",
            f"--session-dir={self._omp_dir() / 'session'}",
            f"--system-prompt={system_prompt_path}",
        ]
        model = self._model_name()
        if model:
            args.append(f"--model={model}")
        if self._session_id is not None:
            args.append(f"--resume={self._session_id}")
        args.append(f"@{prompt_path}")
        return args

    @staticmethod
    def _usage_from_message(message: dict[str, Any]) -> TokenUsage:
        # omp usage buckets are additive and non-overlapping, so map straight.
        usage = message.get("usage")
        if not isinstance(usage, dict):
            return TokenUsage()
        return TokenUsage(
            input_tokens=_as_int(usage.get("input")),
            output_tokens=_as_int(usage.get("output")),
            cache_read_tokens=_as_int(usage.get("cacheRead")),
            cache_write_tokens=_as_int(usage.get("cacheWrite")),
            reasoning_tokens=_as_int(usage.get("reasoning")),
        )

    @staticmethod
    def _message_text(message: dict[str, Any]) -> str:
        content = message.get("content")
        if not isinstance(content, list):
            return ""
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        return "".join(parts)

    @staticmethod
    def _count_tool_calls(messages: list[dict[str, Any]]) -> int:
        count = 0
        for message in messages:
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, list):
                count += sum(
                    1
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "toolCall"
                )
        return count

    async def _run_omp(self, prompt: str) -> IterationResult:
        """Spawn omp for one turn, parse its JSON stream, build the result."""
        system_prompt_path = self._write_system_prompt()
        self._write_mcp_config()
        config_path = self._write_omp_config()
        prompt_path = self._write_turn_prompt(prompt)
        args = self._build_args(system_prompt_path, prompt_path, config_path)

        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(self._job_dir()),
            env=self._build_subprocess_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        messages: list[dict[str, Any]] = []
        usage = TokenUsage()
        final_output = ""
        stream_error = ""
        for line in stdout_bytes.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = event.get("type")
            if etype == "session":
                sid = event.get("id")
                if isinstance(sid, str):
                    self._session_id = sid
            elif etype == "message_end":
                message = event.get("message")
                if isinstance(message, dict):
                    messages.append(message)
                    if message.get("role") == "assistant":
                        usage += self._usage_from_message(message)
                        text = self._message_text(message)
                        if text:
                            final_output = text
            elif etype == "error":
                msg = event.get("message")
                if isinstance(msg, str):
                    stream_error = msg

        if proc.returncode != 0 and not messages:
            error = stream_error or stderr_text.strip() or f"omp exited with code {proc.returncode}"
            logger.error("omp run failed (exit %s): %s", proc.returncode, error)
            return IterationResult(
                outcome=TurnOutcome.FAILED, output="", tool_calls=0, transcript=[], error=error
            )

        self._token_usage += usage
        transcript: list[TranscriptEntry] = OMP.deserialize(messages)
        return IterationResult(
            outcome=TurnOutcome.COMPLETED,
            output=final_output,
            tool_calls=self._count_tool_calls(messages),
            transcript=transcript,
            error=stream_error,
        )

    async def run_iteration(self, prompt: str, *, reset_session: bool = False) -> IterationResult:
        # reset_session drops the session id so the next run starts fresh.
        if reset_session:
            self._session_id = None
        try:
            return await asyncio.wait_for(self._run_omp(prompt), timeout=_TURN_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("omp turn exceeded %ds, cutting the turn", _TURN_TIMEOUT_SECONDS)
            return IterationResult(
                outcome=TurnOutcome.TIMED_OUT, output="", tool_calls=0, transcript=[], error=""
            )
        except Exception as e:
            logger.error("omp run failed: %s", e, exc_info=True)
            return IterationResult(
                outcome=TurnOutcome.FAILED, output="", tool_calls=0, transcript=[], error=str(e)
            )

    async def shutdown(self) -> None:
        logger.debug("OmpAgent shut down")
