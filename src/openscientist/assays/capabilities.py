"""Generate least-privilege job capabilities from assay gateway contracts."""

from __future__ import annotations

import json

from openscientist.assays.registry import AssayRegistry, get_assay_registry
from openscientist.job_container.secrets import make_assay_capability


def make_assay_capability_map(
    master_key: str,
    job_id: str,
    *,
    registry: AssayRegistry | None = None,
) -> str:
    """Return canonical JSON containing one independently scoped token per assay."""

    capabilities = {}
    for adapter in (registry or get_assay_registry()).list():
        permissions = sorted({action.permission for action in adapter.gateway_actions})
        if permissions:
            capabilities[adapter.adapter_id] = make_assay_capability(
                master_key,
                job_id,
                adapter.adapter_id,
                permissions,
            )
    return json.dumps(capabilities, separators=(",", ":"), sort_keys=True)
