"""Explicitly enabled Python entry points; failures stay isolated."""
import logging
from importlib.metadata import entry_points


class AdapterRegistry:
    def __init__(self):
        self._adapters = {}

    def register(self, adapter):
        identifier = getattr(adapter, "id", "")
        if not isinstance(identifier, str) or not identifier or not identifier.replace("-", "").isalnum():
            raise ValueError("Invalid adapter id")
        if identifier in self._adapters or not callable(getattr(adapter, "normalize", None)):
            raise ValueError("Duplicate or invalid adapter")
        self._adapters[identifier] = adapter

    def normalize(self, identifier, payload):
        if identifier not in self._adapters:
            raise KeyError(identifier)
        return self._adapters[identifier].normalize(payload)

    @property
    def ids(self):
        return tuple(self._adapters)

    @classmethod
    def load(cls, enabled=(), builtins=None):
        registry = cls()
        builtins = builtins or {}
        for name, factory in builtins.items():
            if name in enabled:
                registry.register(factory())
        for entry in entry_points(group="mori_companion.adapters"):
            if entry.name not in enabled or entry.name in builtins:
                continue
            try:
                adapter = entry.load()()
                if adapter.id != entry.name:
                    raise ValueError("Entry point name must equal adapter id")
                registry.register(adapter)
            except Exception:
                logging.warning("Could not load adapter %s", entry.name)
        return registry
