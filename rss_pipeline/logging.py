from __future__ import annotations

import json
import logging as std_logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging(*, verbose: bool = False) -> None:
    level = std_logging.DEBUG if verbose else std_logging.INFO
    std_logging.basicConfig(level=level, format=_DEFAULT_FORMAT)


def get_logger(name: str) -> std_logging.Logger:
    return std_logging.getLogger(name)


def _logged_at_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class StructuredRunLogger:
    """Append structured JSONL audit events for a single pipeline run."""

    def __init__(self, output_path: Path, *, run_id: str) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path = output_path
        self.run_id = run_id
        self._handle = output_path.open("w", encoding="utf-8")

    def event(self, name: str, **payload: Any) -> None:
        record = {
            "event": name,
            "run_id": self.run_id,
            "logged_at": _logged_at_iso(),
            **payload,
        }
        self._handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> StructuredRunLogger:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
