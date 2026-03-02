#!/usr/bin/env python3
from __future__ import annotations

from rss_pipeline.cli import run_legacy

if __name__ == "__main__":
    raise SystemExit(run_legacy(["newsdata", "fetch"]))
