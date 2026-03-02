from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_PATHS: tuple[str, ...] = (".env",)


def _clean_env_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().strip('"').strip("'")
    return cleaned or None


def read_env_file_value(path: str | Path, key_name: str) -> str | None:
    env_path = Path(path)
    if not env_path.is_file():
        return None

    try:
        with env_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() != key_name:
                    continue
                return _clean_env_value(value)
    except OSError:
        return None
    return None


def resolve_env_value(
    key_name: str,
    *,
    base_dir: str | Path | None = None,
    env_paths: tuple[str, ...] = DEFAULT_ENV_PATHS,
) -> str | None:
    env_value = _clean_env_value(os.environ.get(key_name))
    if env_value:
        return env_value

    root = Path(base_dir).resolve() if base_dir is not None else Path.cwd()
    for env_path in env_paths:
        candidate = Path(env_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        file_value = read_env_file_value(candidate, key_name)
        if file_value:
            return file_value
    return None


def load_env_file(
    path: str | Path,
    *,
    overwrite: bool = False,
) -> int:
    env_path = Path(path)
    if not env_path.is_file():
        return 0

    loaded = 0
    try:
        with env_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key:
                    continue
                cleaned = _clean_env_value(value)
                if cleaned is None:
                    continue
                if overwrite or key not in os.environ:
                    os.environ[key] = cleaned
                    loaded += 1
    except OSError:
        return loaded

    return loaded


def require_env_value(
    key_name: str,
    *,
    base_dir: str | Path | None = None,
    env_paths: tuple[str, ...] = DEFAULT_ENV_PATHS,
) -> str:
    value = resolve_env_value(key_name, base_dir=base_dir, env_paths=env_paths)
    if not value:
        raise ValueError(f"{key_name} is missing. Add it to environment or .env.")
    return value
