"""Dot-accessible config loading from YAML or JSON files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


class DotDict:
    """Read-only dict wrapper exposing keys as attributes, recursively."""

    def __init__(self, data: dict):
        object.__setattr__(self, "_data", {k: self._wrap(v) for k, v in data.items()})

    @staticmethod
    def _wrap(value: Any) -> Any:
        if isinstance(value, dict):
            return DotDict(value)
        if isinstance(value, list):
            return [DotDict._wrap(item) for item in value]
        return value

    @staticmethod
    def _unwrap(value: Any) -> Any:
        if isinstance(value, DotDict):
            return value.to_dict()
        if isinstance(value, list):
            return [DotDict._unwrap(item) for item in value]
        return value

    def __getattribute__(self, name: str) -> Any:
        # Config keys take priority over dict-like helpers below (keys/items/get/...),
        # so a config value named e.g. "items" is reachable via dot access.
        if not name.startswith("_"):
            data = object.__getattribute__(self, "_data")
            if name in data:
                return data[name]
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"no such config key: {name!r}")

    def __getitem__(self, name: str) -> Any:
        return self._data[name]

    def __contains__(self, name: str) -> bool:
        return name in self._data

    def __iter__(self):
        return iter(self._data)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._data!r})"

    def __call__(self, target: Any) -> Any:
        """Populate `target` with this section's fields and return it.

        `target` may be a class (instantiated with no args, then populated) or an
        existing instance (mutated in place): settings = cfg.data(DataSettings).
        """
        obj = target() if isinstance(target, type) else target
        for key, value in self._data.items():
            setattr(obj, key, value)
        return obj

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def get(self, name: str, default: Any = None) -> Any:
        return self._data.get(name, default)

    def to_dict(self) -> dict:
        return {k: self._unwrap(v) for k, v in self._data.items()}


class Config(DotDict):
    """Loads a YAML or JSON file and exposes it via dot access: Config(path).foo.bar"""

    _LOADERS = {
        ".yaml": yaml.safe_load,
        ".yml": yaml.safe_load,
        ".json": json.loads,
    }

    def __init__(self, path: str | Path):
        path = Path(path)
        try:
            loader = self._LOADERS[path.suffix.lower()]
        except KeyError as exc:
            raise ValueError(
                f"unsupported config extension {path.suffix!r}; expected one of {sorted(self._LOADERS)}"
            ) from exc

        data = loader(path.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError(f"config root must be a mapping, got {type(data).__name__}")
        super().__init__(data)
