"""Explicit registry for governed assay adapters."""

from __future__ import annotations

import threading
from collections.abc import Iterator

from openscientist.assays.contracts import AssayAdapter


class AssayRegistryError(RuntimeError):
    """Base registry error."""


class AssayAlreadyRegisteredError(AssayRegistryError):
    """An adapter id is already occupied by a different descriptor."""


class AssayNotRegisteredError(AssayRegistryError):
    """The requested adapter is unavailable."""


class AssayRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, AssayAdapter] = {}
        self._lock = threading.RLock()

    def register(self, adapter: AssayAdapter) -> AssayAdapter:
        with self._lock:
            existing = self._adapters.get(adapter.adapter_id)
            if existing is not None and existing != adapter:
                raise AssayAlreadyRegisteredError(
                    f"Assay adapter '{adapter.adapter_id}' is already registered."
                )
            self._adapters[adapter.adapter_id] = adapter
            return adapter

    def get(self, adapter_id: str) -> AssayAdapter | None:
        with self._lock:
            return self._adapters.get(adapter_id)

    def require(self, adapter_id: str) -> AssayAdapter:
        adapter = self.get(adapter_id)
        if adapter is None:
            raise AssayNotRegisteredError(f"Assay adapter '{adapter_id}' is not registered.")
        return adapter

    def list(self) -> tuple[AssayAdapter, ...]:
        with self._lock:
            return tuple(self._adapters[key] for key in sorted(self._adapters))

    def __iter__(self) -> Iterator[AssayAdapter]:
        return iter(self.list())


_DEFAULT_REGISTRY = AssayRegistry()
_BUILTINS_LOADED = False
_BUILTINS_LOCK = threading.Lock()


def get_assay_registry(*, load_builtins: bool = True) -> AssayRegistry:
    global _BUILTINS_LOADED
    if load_builtins and not _BUILTINS_LOADED:
        with _BUILTINS_LOCK:
            if not _BUILTINS_LOADED:
                # Built-ins register themselves at this explicit composition boundary.
                from openscientist.integrations.dvc.adapter import register_dvc_adapter
                from openscientist.integrations.open_field.adapter import (
                    register_open_field_adapter,
                )

                register_dvc_adapter(_DEFAULT_REGISTRY)
                register_open_field_adapter(_DEFAULT_REGISTRY)
                _BUILTINS_LOADED = True
    return _DEFAULT_REGISTRY
