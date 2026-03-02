from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any, *, ensure_ascii: bool = True) -> None:
    ensure_parent(path)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=ensure_ascii) + "\n", encoding="utf-8"
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def archive_json(digest_payload: dict[str, Any], output_path: Path, archive_dir: Path) -> Path:
    generated_at = str(
        digest_payload.get("run", {}).get("generated_at")
        or digest_payload.get("generated_at")
        or ""
    )
    date_stamp = generated_at[:10] if generated_at else "unknown-date"
    base_name = output_path.stem
    archive_path = archive_dir / f"{base_name}_{date_stamp}.json"
    write_json(archive_path, digest_payload, ensure_ascii=True)
    return archive_path


def export_prompt_audit_rows(rows: list[dict[str, Any]], output_dir: Path, run_id: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{run_id}.json"
    write_json(path, {"run_id": run_id, "rows": rows}, ensure_ascii=False)
    return path
