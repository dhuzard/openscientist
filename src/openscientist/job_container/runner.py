"""
JobContainerRunner — launches and manages per-job Docker containers.

Each agent job runs in its own ephemeral Docker container for security
isolation.  The container:
- Runs the openscientist-agent image (contains claude-agent-sdk + Node.js)
- Mounts the job directory as /agent/jobs/<job_id>
- Receives provider credentials via env vars
- Communicates status back to the web server via PostgreSQL only

Usage::

    runner = JobContainerRunner()
    container = runner.launch(job_id, job_dir)
    # ... later ...
    runner.cleanup(job_id)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, cast

import docker
from docker import errors as docker_errors
from openscientist.dvc_gateway_client import (
    DVC_CAPABILITY_ENV,
    DVC_GATEWAY_URL_ENV,
    container_dvc_gateway_base_url,
    without_dvc_credentials,
)
from openscientist.exec_broker_client import (
    EXEC_BROKER_URL_ENV,
    EXEC_TOKEN_ENV,
    container_broker_base_url,
)
from openscientist.integrations.fair_prepare import (
    FAIR_PREPARE_URL_ENV,
    validate_fair_prepare_url,
)
from openscientist.job_container.secrets import (
    derive_job_secret,
    make_dvc_capability,
    make_exec_placeholder,
    make_job_placeholder,
)
from openscientist.job_container.utils import resolve_docker_network, to_host_path
from openscientist.llm_proxy import container_proxy_base_url
from openscientist.providers import get_provider
from openscientist.settings import Settings, get_settings
from openscientist.version import SHORT_COMMIT_LENGTH

logger = logging.getLogger(__name__)

AGENT_APP_DIR = "/agent"
_AUTHORING_DATABASE_URL = "postgresql+asyncpg://disabled:disabled@127.0.0.1:1/disabled"
_AUTHORING_SECRET_KEY = "skill-authoring-sentinel-not-a-runtime-credential"
_AUTHORING_COMMON_ENV = {
    "OPENSCIENTIST_PROVIDER",
    "OPENSCIENTIST_MODEL",
    "OPENSCIENTIST_MODEL_CONTEXT_TOKENS",
    "OPENSCIENTIST_LLM_PROXY_URL",
}
_AUTHORING_PROVIDER_ENV: dict[str, set[str]] = {
    "anthropic": {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
    },
    "cborg": {
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
    },
    "vertex": {
        "CLAUDE_CODE_USE_VERTEX",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "CLOUD_ML_REGION",
        "VERTEX_REGION_CLAUDE_4_5_SONNET",
        "VERTEX_REGION_CLAUDE_4_5_HAIKU",
    },
    "bedrock": {
        "CLAUDE_CODE_USE_BEDROCK",
        "ANTHROPIC_BEDROCK_BASE_URL",
        "AWS_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_PROFILE",
        "AWS_BEARER_TOKEN_BEDROCK",
    },
    "foundry": {
        "CLAUDE_CODE_USE_FOUNDRY",
        "ANTHROPIC_FOUNDRY_RESOURCE",
        "ANTHROPIC_FOUNDRY_BASE_URL",
        "ANTHROPIC_FOUNDRY_API_KEY",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
    },
    "openai": {"OPENAI_API_KEY"},
    "azure-openai": {
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_RESOURCE",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_VERSION",
    },
    "ollama": {
        "OPENAI_API_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
    },
}


class JobContainerRunner:
    """Launches and stops per-job agent containers."""

    def __init__(self) -> None:
        self._docker: docker.DockerClient = docker.from_env()

    @staticmethod
    def _is_not_found_error(error: Exception) -> bool:
        """Return True when Docker reports that a container no longer exists."""
        return isinstance(error, docker_errors.NotFound)

    def _get_network(self, configured_network: str | None) -> str:
        """Resolve the Docker network for agent containers."""
        return resolve_docker_network(self._docker, configured_network)

    @staticmethod
    def _authoring_provider_environment(provider_env: dict[str, str]) -> dict[str, str]:
        """Keep only the active provider's minimum direct-completion settings."""

        provider_id = provider_env.get("OPENSCIENTIST_PROVIDER", "").lower()
        allowed = _AUTHORING_COMMON_ENV | _AUTHORING_PROVIDER_ENV.get(provider_id, set())
        return {key: value for key, value in provider_env.items() if key in allowed}

    @staticmethod
    def _build_container_environment(
        settings: Settings,
        *,
        job_id: str,
        job_mount: str,
        provider_env: dict[str, str],
        run_mode: str = "discovery",
    ) -> dict[str, str]:
        """Build the environment variables for the agent container."""
        cs = settings.container
        # Build the minimum environment for the selected run mode.
        if run_mode == "skill_authoring":
            provider_env = {
                **provider_env,
                "OPENSCIENTIST_PROVIDER": provider_env.get(
                    "OPENSCIENTIST_PROVIDER",
                    settings.provider.provider_id,
                ),
            }
            provider_env = JobContainerRunner._authoring_provider_environment(provider_env)

        # A future provider/settings refactor must not accidentally carry DVC
        # credentials, CA paths, or the vendor base URL into the agent.
        provider_env = without_dvc_credentials(provider_env)
        env: dict[str, str] = {
            "JOB_ID": job_id,
            "JOB_DIR": job_mount,
            **provider_env,
        }
        if run_mode == "skill_authoring":
            # Settings/provider construction requires these two fields, but an
            # authoring turn never accesses application data or derives auth
            # credentials. Use deliberately unusable sentinels instead of the
            # real database URL or per-job secret.
            env.update(
                {
                    "DATABASE_URL": _AUTHORING_DATABASE_URL,
                    "OPENSCIENTIST_SECRET_KEY": _AUTHORING_SECRET_KEY,
                }
            )
        else:
            env.update(
                {
                    "DATABASE_URL": settings.database.effective_database_url,
                    "OPENSCIENTIST_SECRET_KEY": derive_job_secret(settings.secret_key, job_id),
                    # Per-job execution credential the broker verifies, plus the broker URL.
                    EXEC_TOKEN_ENV: make_exec_placeholder(settings.secret_key, job_id),
                    EXEC_BROKER_URL_ENV: container_broker_base_url(),
                    # The only DVC-related values an agent may receive.
                    DVC_CAPABILITY_ENV: make_dvc_capability(settings.secret_key, job_id),
                    DVC_GATEWAY_URL_ENV: container_dvc_gateway_base_url(),
                }
            )
            # FAIR-VCG is addressed through a non-secret internal service URL.
            # Forward this one validated locator explicitly; do not copy
            # arbitrary FAIR-related environment variables into the agent.
            fair_prepare_url = os.environ.get(FAIR_PREPARE_URL_ENV)
            if fair_prepare_url:
                env[FAIR_PREPARE_URL_ENV] = validate_fair_prepare_url(fair_prepare_url)
        # Only set the run-mode override when it diverges from the default so
        # ordinary discovery launches keep a clean env. The entrypoint reads
        # OPENSCIENTIST_RUN_MODE. "report_only" re-runs just the report phase.
        if run_mode != "discovery":
            env["OPENSCIENTIST_RUN_MODE"] = run_mode
        # Forward the per-turn Codex timeout so the agent (CodexAgent reads
        # OPENSCIENTIST_CODEX_TURN_TIMEOUT at import) can be tuned for slow
        # local backends. Without this the agent always uses the 900s default.
        turn_timeout = os.environ.get("OPENSCIENTIST_CODEX_TURN_TIMEOUT")
        if turn_timeout:
            env["OPENSCIENTIST_CODEX_TURN_TIMEOUT"] = turn_timeout
        if cs.host_project_dir:
            env["OPENSCIENTIST_HOST_PROJECT_DIR"] = cs.host_project_dir
            env["OPENSCIENTIST_CONTAINER_APP_DIR"] = AGENT_APP_DIR
        # Air-gapped mode routes the tools subprocess to the local PubMed corpus.
        if settings.airgap.enabled:
            env["OPENSCIENTIST_AIRGAPPED"] = "1"
        if settings.provider.google_application_credentials:
            env["GOOGLE_APPLICATION_CREDENTIALS"] = "/agent/gcp-credentials.json"
        if settings.phenix.phenix_host_path:
            env["PHENIX_PATH"] = "/opt/phenix"
        return env

    @staticmethod
    def _build_container_volumes(
        settings: Settings,
        *,
        job_dir_host: Path,
        job_mount: str,
        include_phenix: bool = True,
        include_gcp_credentials: bool = True,
    ) -> dict[str, dict[str, str]]:
        """Build the bind mounts for the agent container."""
        volumes: dict[str, dict[str, str]] = {
            str(job_dir_host): {"bind": job_mount, "mode": "rw"},
        }
        gcp_path = (
            settings.provider.google_application_credentials if include_gcp_credentials else None
        )
        if gcp_path:
            gcp_host_path = settings.provider.gcp_credentials_host_path or gcp_path
            volumes[str(gcp_host_path)] = {
                "bind": "/agent/gcp-credentials.json",
                "mode": "ro",
            }
        phenix_host = settings.phenix.phenix_host_path if include_phenix else None
        if phenix_host:
            volumes[str(Path(phenix_host).expanduser().resolve())] = {
                "bind": "/opt/phenix",
                "mode": "ro",
            }
        return volumes

    @staticmethod
    def _agent_runtime_settings(
        settings: Settings,
    ) -> tuple[str | None, str, float, str | None]:
        """Return network, memory, CPU, and platform settings for the agent."""
        container_settings = settings.container
        if hasattr(container_settings, "model_dump"):
            config = container_settings.model_dump()
        else:
            config = vars(container_settings)
        return (
            cast(str | None, config["agent_network"]),
            cast(str, config["agent_memory"]),
            cast(float, config["agent_cpu"]),
            cast(str | None, config["agent_platform"]),
        )

    @staticmethod
    def _build_launch_configuration(
        settings: Settings,
        *,
        job_id: str,
        job_dir_host: Path,
        run_mode: str = "discovery",
    ) -> tuple[
        dict[str, str],
        dict[str, dict[str, str]],
        str | None,
        str,
        float,
        str | None,
    ]:
        """Build the environment, mounts, and runtime settings for launch()."""
        agent_network, agent_memory, agent_cpu, agent_platform = (
            JobContainerRunner._agent_runtime_settings(settings)
        )
        job_mount = f"{AGENT_APP_DIR}/jobs/{job_id}"
        provider_env = get_provider().proxied_container_env(
            proxy_base_url=container_proxy_base_url(),
            placeholder=make_job_placeholder(settings.secret_key, job_id),
        )
        env = JobContainerRunner._build_container_environment(
            settings,
            job_id=job_id,
            job_mount=job_mount,
            provider_env=provider_env,
            run_mode=run_mode,
        )
        volumes = JobContainerRunner._build_container_volumes(
            settings,
            job_dir_host=job_dir_host,
            job_mount=job_mount,
            include_phenix=run_mode != "skill_authoring",
            include_gcp_credentials=(
                run_mode != "skill_authoring" or settings.provider.provider_id == "vertex"
            ),
        )
        return env, volumes, agent_network, agent_memory, agent_cpu, agent_platform

    @staticmethod
    def _airgap_firewall_config(
        settings: Settings,
    ) -> tuple[list[str] | None, str | None, list[str] | None, dict[str, str]]:
        """Firewall launch overrides (cap_add, user, entrypoint, extra_env) for
        air-gapped mode, or neutral values when off."""
        if not settings.airgap.enabled:
            return None, None, None, {}
        from openscientist.job_container.egress import (
            derive_egress_allowlist,
            format_egress_allowlist,
        )

        allow = format_egress_allowlist(derive_egress_allowlist(settings))
        return (
            ["NET_ADMIN"],
            "root",
            ["/agent-firewall-entrypoint.sh"],
            {"OPENSCIENTIST_FIREWALL_ALLOW": allow},
        )

    def launch(self, job_id: str, job_dir: Path, *, run_mode: str = "discovery") -> Any:
        """
        Launch an agent container for the given job.

        The container runs docker/agent-entrypoint.py which calls
        run_discovery_async(job_dir), or regenerate_report_async(job_dir) when
        run_mode is "report_only".

        Args:
            job_id: Job UUID string (used for container name + labels)
            job_dir: Absolute host path to the job directory
            run_mode: "discovery" (full loop) or "report_only" (report phase
                only, against the already-persisted findings)

        Returns:
            docker.models.containers.Container object

        Raises:
            RuntimeError: If Docker is unavailable or launch fails
        """
        settings: Settings = get_settings()
        cs = settings.container

        # Translate job_dir from container-internal path to host path.
        # Must resolve to absolute FIRST (so relative paths like "jobs/uuid" become
        # "/app/jobs/uuid" inside the web container), then translate to the host
        # path.  Docker requires absolute paths for bind mounts; relative paths
        # are misinterpreted as named volumes.
        job_dir_resolved = job_dir.resolve()
        # Host-side agent prep may copy backend credentials into the mounted
        # directory. Authoring uses a direct, no-tools completion path and must
        # never receive those files.
        if run_mode != "skill_authoring":
            from openscientist.agent.factory import agent_class_for_provider_id

            agent_class_for_provider_id(settings.provider.provider_id).provision_host_prelaunch(
                settings, job_dir_resolved
            )
        job_dir_host = to_host_path(job_dir_resolved, cs)
        env, volumes, agent_network, agent_memory, agent_cpu, agent_platform = (
            self._build_launch_configuration(
                settings,
                job_id=job_id,
                job_dir_host=job_dir_host,
                run_mode=run_mode,
            )
        )
        network = self._get_network(agent_network)
        cap_add, run_user, entrypoint, firewall_env = self._airgap_firewall_config(settings)
        env.update(firewall_env)

        container = self._docker.containers.run(
            image=cs.agent_image,
            name=f"openscientist-agent-{job_id[:SHORT_COMMIT_LENGTH]}",
            detach=True,
            remove=False,
            environment=env,
            volumes=volumes,
            network=network,
            mem_limit=agent_memory,
            nano_cpus=int(agent_cpu * 1e9),
            platform=agent_platform or None,
            security_opt=["no-new-privileges:true"],
            cap_add=cap_add,
            user=run_user,
            entrypoint=entrypoint,
            # Map host.docker.internal to the host gateway so a job can reach a
            # model server running on the host (e.g. a local Ollama at
            # http://host.docker.internal:11434/v1). Harmless for providers that
            # do not use it. On Linux this is not provided by default.
            extra_hosts={"host.docker.internal": "host-gateway"},
            labels={
                "openscientist.job_id": job_id,
                "openscientist.type": "agent",
            },
        )

        logger.info("Launched agent container %s for job %s", container.short_id, job_id)
        return container

    def run_skill_authoring_turn(
        self,
        job_id: str,
        job_dir: Path,
        *,
        timeout: int = 300,
    ) -> None:
        """Run one LLM-assisted skill authoring turn in an isolated container."""

        container = self.launch(job_id, job_dir, run_mode="skill_authoring")
        try:
            try:
                outcome = container.wait(timeout=timeout)
            except Exception as error:
                raise RuntimeError(
                    f"Skill authoring turn did not finish within {timeout}s"
                ) from error
            exit_code = int(outcome.get("StatusCode", 1)) if isinstance(outcome, dict) else 1
            if exit_code != 0:
                logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Skill authoring container exited with code {exit_code}: {logs[-2000:]}"
                )
        finally:
            try:
                container.remove(force=True)
            except docker_errors.APIError as error:
                if not self._is_not_found_error(error):
                    logger.warning(
                        "Failed to remove skill authoring container for %s: %s",
                        job_id,
                        error,
                    )

    def stop(self, job_id: str, timeout: int = 10) -> None:
        """Stop the container for a job (graceful → SIGKILL)."""
        container = self._find_container(job_id)
        if container:
            try:
                container.stop(timeout=timeout)
                logger.info("Stopped container for job %s", job_id)
            except docker_errors.APIError as error:
                if self._is_not_found_error(error):
                    return
                logger.warning("Failed to stop container for job %s: %s", job_id, error)

    def cleanup(self, job_id: str, log_dir: Path | None = None) -> None:
        """Remove the container for a job, optionally saving its logs first."""
        container = self._find_container(job_id)
        if container:
            try:
                if log_dir is not None:
                    try:
                        logs = container.logs(stdout=True, stderr=True).decode(
                            "utf-8", errors="replace"
                        )
                        (log_dir / "agent-container.log").write_text(logs)
                    except (docker_errors.APIError, OSError) as error:
                        if not self._is_not_found_error(error):
                            logger.warning(
                                "Failed to save container logs for job %s: %s",
                                job_id,
                                error,
                            )
                container.remove(force=True)
                logger.info("Removed container for job %s", job_id)
            except docker_errors.APIError as error:
                if self._is_not_found_error(error):
                    return
                logger.warning("Failed to remove container for job %s: %s", job_id, error)

    def get_exit_code(self, job_id: str) -> int | None:
        """
        Return the exit code of the agent container if it has stopped, else None.

        Returns None if the container is still running or cannot be found.
        """
        container = self._find_container(job_id)
        if container is None:
            return None
        try:
            container.reload()
            if container.status in ("exited", "dead"):
                exit_code = container.attrs.get("State", {}).get("ExitCode")
                if isinstance(exit_code, int):
                    return exit_code
                if exit_code is not None:
                    try:
                        return int(exit_code)
                    except (TypeError, ValueError):
                        logger.warning(
                            "Unexpected non-integer exit code for job %s: %r",
                            job_id,
                            exit_code,
                        )
        except docker_errors.APIError as error:
            if self._is_not_found_error(error):
                return None
            logger.warning("Failed to get exit code for job %s: %s", job_id, error)
        return None

    def _find_container(self, job_id: str) -> Any | None:
        """Find the agent container for a job by labels."""
        try:
            containers = self._docker.containers.list(
                all=True,
                filters={
                    "label": [
                        f"openscientist.job_id={job_id}",
                        "openscientist.type=agent",
                    ]
                },
            )
            return containers[0] if containers else None
        except docker_errors.DockerException as error:
            logger.warning("Failed to find container for job %s: %s", job_id, error)
            return None
