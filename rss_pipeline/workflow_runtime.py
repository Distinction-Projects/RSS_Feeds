from __future__ import annotations

import hashlib
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_run_id(prefix: str) -> str:
    now = utc_now_iso()
    raw = f"{prefix}|{now}|{os.getpid()}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{now[:19].replace(':', '').replace('-', '')}-{digest}"


def command_line() -> str:
    return " ".join(sys.argv)


@dataclass(slots=True)
class RunContext:
    run_id: str
    started_at: float
    generated_at: str

    @classmethod
    def start(cls, prefix: str) -> RunContext:
        return cls(
            run_id=build_run_id(prefix), started_at=time.monotonic(), generated_at=utc_now_iso()
        )

    @property
    def duration_seconds(self) -> float:
        return round(time.monotonic() - self.started_at, 3)


def repo_root_from(file_path: str) -> Path:
    return Path(file_path).resolve().parent.parent
