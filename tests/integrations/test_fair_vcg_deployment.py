from pathlib import Path
from typing import Any, cast

import yaml

from openscientist.integrations.fair_prepare import FAIR_VCG_PINNED_COMMIT

OVERLAY = Path(__file__).parents[2] / "docker-compose.fair-vcg.yml"
MAKEFILE = Path(__file__).parents[2] / "Makefile"


def _compose() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(OVERLAY.read_text(encoding="utf-8")))


def test_fair_vcg_build_is_pinned_and_not_host_published():
    service = _compose()["services"]["fair-vcg-mentor"]

    assert FAIR_VCG_PINNED_COMMIT in service["build"]["context"]
    assert service["expose"] == ["8000"]
    assert "ports" not in service
    assert service["networks"]["agent-runtime"]["aliases"] == ["fair-vcg-mentor"]


def test_application_startup_is_gated_by_full_contract_canary():
    services = _compose()["services"]

    assert services["fair-vcg-canary"]["depends_on"]["fair-vcg-mentor"]["condition"] == (
        "service_healthy"
    )
    assert services["openscientist"]["depends_on"]["fair-vcg-canary"]["condition"] == (
        "service_completed_successfully"
    )
    command = services["fair-vcg-canary"]["command"]
    assert "openscientist.integrations.fair_prepare_canary" in command
    assert command[command.index("--attempts") + 1] == "1"


def test_spawned_agents_use_the_attachable_runtime_bridge():
    compose = _compose()
    service = compose["services"]["openscientist"]

    assert service["environment"]["FAIR_PREPARE_URL"] == "http://fair-vcg-mentor:8000"
    assert service["environment"]["OPENSCIENTIST_AGENT_NETWORK"].startswith(
        "${OPENSCIENTIST_AGENT_NETWORK:-"
    )
    assert compose["networks"]["agent-runtime"]["attachable"] is True


def test_trusted_gateway_build_receives_private_udwa_as_a_secret():
    compose = _compose()
    build = compose["services"]["openscientist"]["build"]

    assert build["args"]["INSTALL_UDWA"] == "true"
    assert build["secrets"] == ["github_token"]
    assert compose["secrets"]["github_token"]["environment"] == "GITHUB_TOKEN"


def test_fair_make_targets_never_drop_the_runtime_overlay():
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "FAIR_COMPOSE_FILES ?= -f docker-compose.yml -f docker-compose.fair-vcg.yml" in makefile
    for target in ("start-fair:", "restart-fair:", "fair-status:"):
        section = makefile.split(target, 1)[1].split("\n\n", 1)[0]
        assert "docker compose $(FAIR_COMPOSE_FILES)" in section


def test_default_dvc_lifecycle_cannot_silently_drop_fair_overlay():
    makefile = MAKEFILE.read_text(encoding="utf-8")

    for target in ("start:", "stop:", "build:", "rebuild:", "status:"):
        section = makefile.split(f"\n{target}", 1)[1].split("\n\n", 1)[0]
        docker_lines = [line for line in section.splitlines() if "docker compose" in line]
        assert docker_lines
        assert all("$(FAIR_COMPOSE_FILES)" in line for line in docker_lines)

    assert "restart: restart-fair" in makefile
    deploy = makefile.split("\ndeploy:", 1)[1]
    assert "make rebuild" in deploy
    assert "docker compose $(FAIR_COMPOSE_FILES) exec openscientist" in deploy
