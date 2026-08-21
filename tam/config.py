"""Dot-accessible config loading from YAML or JSON files, with base-chain
inheritance, env-var expansion, file includes, and named-variable templating
-- so a family of similar configs (e.g. many llm_trading variants) can share
common structure instead of repeating it in every file.

Supported in any YAML/JSON config:
  base: other.yaml            -- single base file (deep-merged, this file wins)
  base: [a.yaml, b.yaml]       -- multiple bases merged left-to-right, then this file
  ${VAR} / $VAR                -- env-var expansion (unset -> empty string)
  << file.yaml                 -- inline file include (whole file)
  << file.yaml#section.key     -- include a nested section (dot-separated)
  vars: {...} + {{vars.x.y}}   -- name a value once, reference it elsewhere in
                                   the same (post-base-merge) document

Resolution order per file: env-var expansion, then includes, then base-chain
merging (recursively -- a base file goes through the same pipeline). `vars:`
is resolved exactly once, on the fully-assembled top-level document, and
dropped from the result -- a base file's own `vars:` block is often
intentionally incomplete (e.g. it names {{vars.env}} but leaves `env` for
whatever leafs onto it to supply), so resolving per-file would raise on a var
that a later merge step was always going to fill in.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, FrozenSet, Optional

import yaml

_ENV_RE = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_INCLUDE_RE = re.compile(r"^<<\s+(\S+?)(?:#(.+))?$")
_TEMPLATE_VAR_RE = re.compile(r"\{\{vars\.([^}]+)\}\}")
_WHOLE_TEMPLATE_VAR_RE = re.compile(r"^\{\{vars\.([^}]+)\}\}$")

_LOADERS = {".yaml": yaml.safe_load, ".yml": yaml.safe_load, ".json": json.loads}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` into `base` (override wins on collision);
    both left unmodified, a new dict is returned. Lists are atomic -- an
    override list fully replaces a base list, never merges element-wise."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _expand_env(obj: Any) -> Any:
    """Recursively expand `${VAR}`/`$VAR` from os.environ (unset -> "")."""
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    if isinstance(obj, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1) or m.group(2), ""), obj)
    return obj


def _resolve_includes(obj: Any, base_dir: Path, seen: FrozenSet[Path]) -> Any:
    """Recursively resolve `<< file.yaml` / `<< file.yaml#section` includes."""
    if isinstance(obj, dict):
        return {k: _resolve_includes(v, base_dir, seen) for k, v in obj.items()}
    if isinstance(obj, list):
        has_include = any(isinstance(v, str) and _INCLUDE_RE.match(v.strip()) for v in obj)
        resolved = [_resolve_includes(v, base_dir, seen) for v in obj]
        if has_include and resolved and all(isinstance(v, dict) for v in resolved):
            merged: dict = {}
            for d in resolved:
                merged = _deep_merge(merged, d)
            return merged
        return resolved
    if isinstance(obj, str):
        match = _INCLUDE_RE.match(obj.strip())
        if match:
            include_path = (base_dir / match.group(1)).resolve()
            included = _load_with_base(include_path, seen)
            section = match.group(2)
            if section is None:
                return included
            result: Any = included
            for key in section.split("."):
                if not isinstance(result, dict) or key not in result:
                    raise ValueError(f"include section {section!r} not found in {include_path} (missing key {key!r})")
                result = result[key]
            return result
    return obj


def _lookup_var(dotted_path: str, vars_dict: dict) -> Any:
    parts = dotted_path.split(".")
    value: Any = vars_dict
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"template variable '{{{{vars.{dotted_path}}}}}' not found; available vars: {list(vars_dict)}")
        value = value[part]
    return value


def _substitute(text: str, vars_dict: dict) -> Any:
    """A string that's *exactly* one `{{vars.x}}` token splices in the
    referenced value with its real type (dict/list/int/...); a token embedded
    in a longer string interpolates as a string, same as f-string-style use."""
    whole = _WHOLE_TEMPLATE_VAR_RE.match(text)
    if whole:
        return _lookup_var(whole.group(1), vars_dict)
    return _TEMPLATE_VAR_RE.sub(lambda m: str(_lookup_var(m.group(1), vars_dict)), text)


def _resolve_vars_tree(obj: Any, vars_dict: dict) -> Any:
    if isinstance(obj, dict):
        return {k: _resolve_vars_tree(v, vars_dict) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_vars_tree(v, vars_dict) for v in obj]
    if isinstance(obj, str):
        return _substitute(obj, vars_dict)
    return obj


def _resolve_vars_dict(vars_dict: dict, max_passes: int = 10) -> dict:
    """Vars that reference other vars resolve iteratively until stable, so
    declaration order within `vars:` doesn't matter."""
    for _ in range(max_passes):
        resolved = _resolve_vars_tree(vars_dict, vars_dict)
        if resolved == vars_dict:
            return resolved
        vars_dict = resolved
    return vars_dict


def _resolve_vars(obj: Any, vars_dict: dict) -> Any:
    return _resolve_vars_tree(obj, _resolve_vars_dict(vars_dict))


def _read_raw(path: Path) -> dict:
    try:
        loader = _LOADERS[path.suffix.lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported config extension {path.suffix!r}; expected one of {sorted(_LOADERS)}") from exc
    data = loader(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config root must be a mapping, got {type(data).__name__}")
    return data


def _load_with_base(path: Path, _seen: Optional[FrozenSet[Path]] = None) -> dict:
    is_top_level = _seen is None
    path = path.resolve()
    seen = _seen or frozenset()

    if path in seen:
        raise ValueError(f"circular base/include reference: {path} already visited in chain {sorted(str(p) for p in seen)}")
    seen = seen | {path}

    raw = _read_raw(path)
    raw = _expand_env(raw)
    raw = _resolve_includes(raw, path.parent, seen)

    base_val = raw.pop("base", None)
    if base_val is None:
        result = raw
    else:
        if isinstance(base_val, str):
            base_refs = [base_val]
        elif isinstance(base_val, list):
            base_refs = base_val
        else:
            raise ValueError(f"'base' must be a string or list of strings; got {type(base_val).__name__}")

        merged: dict = {}
        for ref in base_refs:
            if not isinstance(ref, str):
                raise ValueError(f"invalid base entry {ref!r}: expected a string path")
            merged = _deep_merge(merged, _load_with_base((path.parent / ref).resolve(), seen))
        result = _deep_merge(merged, raw)

    if is_top_level:
        result = _resolve_vars(result, result.get("vars", {}))
        result.pop("vars", None)
    return result


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

    def setdefault(self, name: str, value: Any) -> Any:
        """Set `name` to `value` only if not already present, and return the
        (possibly just-set) value -- mirrors dict.setdefault. For the rare case
        a caller needs to inject a computed default into an otherwise
        read-only-by-convention config section, e.g. deriving an artifact path
        from the config file's own hash when the config didn't specify one."""
        return self._data.setdefault(name, value)

    def to_dict(self) -> dict:
        return {k: self._unwrap(v) for k, v in self._data.items()}


class Config(DotDict):
    """Loads a YAML or JSON file (with base/vars/include/env-var resolution)
    and exposes it via dot access: Config(path).foo.bar"""

    def __init__(self, path: str | Path):
        data = _load_with_base(Path(path))
        super().__init__(data)
