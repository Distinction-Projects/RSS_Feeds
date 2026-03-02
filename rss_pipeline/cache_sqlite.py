from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .workflow_runtime import utc_now_iso


class SQLiteOpenAICache:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS openai_cache (
                    cache_key TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS openai_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    called_at TEXT NOT NULL,
                    purpose TEXT,
                    model TEXT,
                    cache_key TEXT,
                    cache_hit INTEGER NOT NULL,
                    request_hash TEXT,
                    prompt_hash TEXT,
                    user_prompt_hash TEXT,
                    response_hash TEXT,
                    latency_ms INTEGER,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS prompt_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    created_at TEXT NOT NULL,
                    purpose TEXT,
                    model TEXT,
                    cache_key TEXT,
                    article_id TEXT,
                    lens_name TEXT,
                    rubric_name TEXT,
                    prompt_ref TEXT,
                    prompt_hash TEXT,
                    prompt_body TEXT,
                    response_ref TEXT,
                    response_hash TEXT,
                    response_body TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_openai_calls_run_id
                    ON openai_calls(run_id);
                CREATE INDEX IF NOT EXISTS idx_prompt_audit_run_id
                    ON prompt_audit(run_id);
                """
            )

    def get_cached(self, cache_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT response_json, hit_count FROM openai_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None

            conn.execute(
                "UPDATE openai_cache SET hit_count = hit_count + 1, updated_at = ? WHERE cache_key = ?",
                (utc_now_iso(), cache_key),
            )

        try:
            payload = json.loads(row["response_json"])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            return None
        return None

    def set_cached(
        self,
        *,
        cache_key: str,
        model: str,
        request_hash: str,
        response_payload: dict[str, Any],
    ) -> None:
        now = utc_now_iso()
        serialized = json.dumps(response_payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO openai_cache (cache_key, model, request_hash, response_json, created_at, updated_at, hit_count)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(cache_key) DO UPDATE SET
                    model=excluded.model,
                    request_hash=excluded.request_hash,
                    response_json=excluded.response_json,
                    updated_at=excluded.updated_at
                """,
                (cache_key, model, request_hash, serialized, now, now),
            )

    def log_openai_call(
        self,
        *,
        run_id: str,
        purpose: str,
        model: str,
        cache_key: str,
        cache_hit: bool,
        request_hash: str,
        prompt_hash: str,
        user_prompt_hash: str,
        response_hash: str,
        latency_ms: int,
        error: str | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO openai_calls (
                    run_id, called_at, purpose, model, cache_key, cache_hit,
                    request_hash, prompt_hash, user_prompt_hash, response_hash, latency_ms, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    utc_now_iso(),
                    purpose,
                    model,
                    cache_key,
                    1 if cache_hit else 0,
                    request_hash,
                    prompt_hash,
                    user_prompt_hash,
                    response_hash,
                    latency_ms,
                    error,
                ),
            )

    def record_prompt_audit(
        self,
        *,
        run_id: str,
        purpose: str,
        model: str,
        cache_key: str,
        prompt_ref: str,
        prompt_hash: str,
        prompt_body: str,
        response_ref: str,
        response_hash: str,
        response_body: str,
        article_id: str | None = None,
        lens_name: str | None = None,
        rubric_name: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO prompt_audit (
                    run_id, created_at, purpose, model, cache_key,
                    article_id, lens_name, rubric_name,
                    prompt_ref, prompt_hash, prompt_body,
                    response_ref, response_hash, response_body
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    utc_now_iso(),
                    purpose,
                    model,
                    cache_key,
                    article_id,
                    lens_name,
                    rubric_name,
                    prompt_ref,
                    prompt_hash,
                    prompt_body,
                    response_ref,
                    response_hash,
                    response_body,
                ),
            )

    def prompt_audit_rows(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM prompt_audit WHERE run_id = ? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def run_cache_stats(self, run_id: str) -> dict[str, int]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS calls,
                    SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) AS hits,
                    SUM(CASE WHEN cache_hit = 0 THEN 1 ELSE 0 END) AS misses
                FROM openai_calls
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return {"calls": 0, "hits": 0, "misses": 0}
        calls = int(row["calls"] or 0)
        hits = int(row["hits"] or 0)
        misses = int(row["misses"] or 0)
        return {"calls": calls, "hits": hits, "misses": misses}
