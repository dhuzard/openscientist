"""Contract tests for generic assay gateway capabilities."""

import json

import pytest

from openscientist.assays import get_assay_registry
from openscientist.assays.capabilities import make_assay_capability_map
from openscientist.job_container.secrets import (
    make_assay_capability,
    verify_assay_capability,
)

_MASTER = "assay-capability-master"
_NOW = 2_000_000_000


def _verify(token: str, **overrides: object) -> bool:
    arguments = {
        "expected_job_id": "job-1",
        "expected_assay_id": "dvc",
        "required_permission": "dataset:import",
        "now": _NOW,
        **overrides,
    }
    return verify_assay_capability(_MASTER, token, **arguments)  # type: ignore[arg-type]


def test_assay_capability_binds_job_assay_permission_and_expiry() -> None:
    token = make_assay_capability(
        _MASTER,
        "job-1",
        "dvc",
        ("dataset:read", "dataset:import"),
        now=_NOW,
        ttl_seconds=600,
    )

    assert _verify(token)
    assert not _verify(token, expected_job_id="job-2")
    assert not _verify(token, expected_assay_id="open-field")
    assert not _verify(token, required_permission="dataset:delete")
    assert not _verify(token, now=_NOW + 601)


def test_assay_capability_is_tamper_evident() -> None:
    token = make_assay_capability(
        _MASTER,
        "job-1",
        "dvc",
        ("dataset:import",),
        now=_NOW,
    )

    replacement = "0" if token[-1] != "0" else "1"
    assert not _verify(token[:-1] + replacement)


@pytest.mark.parametrize("permissions", [(), ("invalid permission",)])
def test_assay_capability_rejects_invalid_permission_scopes(
    permissions: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="permission"):
        make_assay_capability(_MASTER, "job-1", "dvc", permissions, now=_NOW)


def test_capability_map_is_generated_from_each_registered_gateway_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openscientist.job_container.secrets.time.time", lambda: _NOW)
    registry = get_assay_registry()
    capabilities = json.loads(make_assay_capability_map(_MASTER, "job-1", registry=registry))

    assert set(capabilities) == {adapter.adapter_id for adapter in registry.list()}
    for adapter in registry.list():
        for permission in {action.permission for action in adapter.gateway_actions}:
            assert verify_assay_capability(
                _MASTER,
                capabilities[adapter.adapter_id],
                expected_job_id="job-1",
                expected_assay_id=adapter.adapter_id,
                required_permission=permission,
                now=_NOW,
            )
        other_assay = "open-field" if adapter.adapter_id == "dvc" else "dvc"
        assert not verify_assay_capability(
            _MASTER,
            capabilities[adapter.adapter_id],
            expected_job_id="job-1",
            expected_assay_id=other_assay,
            required_permission=next(iter(adapter.gateway_actions)).permission,
            now=_NOW,
        )
