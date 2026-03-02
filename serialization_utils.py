from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter, ValidationError

_ADAPTER_CACHE: dict[str, TypeAdapter[Any]] = {}


def _adapter_cache_key(type_hint: Any) -> str:
    return repr(type_hint)


def adapter_for(type_hint: Any) -> TypeAdapter[Any]:
    key = _adapter_cache_key(type_hint)
    adapter = _ADAPTER_CACHE.get(key)
    if adapter is None:
        adapter = TypeAdapter(type_hint)
        _ADAPTER_CACHE[key] = adapter
    return adapter


def validate_json(type_hint: Any, raw: str | bytes, *, context: str) -> Any:
    try:
        return adapter_for(type_hint).validate_json(raw)
    except ValidationError as exc:
        raise ValueError(f"{context}: strict JSON validation failed: {exc}") from exc


def validate_python(type_hint: Any, value: Any, *, context: str) -> Any:
    try:
        return adapter_for(type_hint).validate_python(value)
    except ValidationError as exc:
        raise ValueError(f"{context}: strict payload validation failed: {exc}") from exc


def dump_json(type_hint: Any, value: Any, *, indent: int | None = None, context: str) -> str:
    normalized = validate_python(type_hint, value, context=context)
    return adapter_for(type_hint).dump_json(normalized, indent=indent).decode("utf-8")
