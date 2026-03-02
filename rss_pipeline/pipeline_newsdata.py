from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .artifact_store import write_json
from .env import require_env_value
from .workflow_runtime import utc_now_iso

BASE_URL = "https://newsdata.io/api/1/news"
ENV_KEY = "NEWSDATA_API_KEY"
DEFAULT_OUTPUT = Path("data/newsdata_dump.json")


class NewsDataError(RuntimeError):
    pass


def _empty_dump() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "updated_at": None,
        "articles": [],
        "requests": [],
    }


def load_dump(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_dump()

    payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    if isinstance(payload, list):
        migrated = _empty_dump()
        migrated["articles"] = payload
        return migrated

    if not isinstance(payload, dict):
        raise NewsDataError(f"Unexpected JSON format in {path}")

    payload.setdefault("schema_version", "1.0")
    payload.setdefault("updated_at", None)
    payload.setdefault("articles", [])
    payload.setdefault("requests", [])

    if not isinstance(payload.get("articles"), list):
        payload["articles"] = []
    if not isinstance(payload.get("requests"), list):
        payload["requests"] = []

    return payload


def article_key(item: dict[str, Any]) -> str:
    article_id = str(item.get("article_id") or "").strip()
    if article_id:
        return f"id:{article_id}"

    link = str(item.get("link") or "").strip()
    if link:
        return f"link:{link}"

    title = str(item.get("title") or "").strip()
    pub_date = str(item.get("pubDate") or item.get("published_at") or "").strip()
    source = str(item.get("source_id") or item.get("source_name") or "").strip()
    return f"fallback:{title}|{pub_date}|{source}"


def fetch_newsdata(params: dict[str, str]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}?{query}"
    with urllib.request.urlopen(url, timeout=20) as response:
        payload = json.load(response)

    if not isinstance(payload, dict):
        raise NewsDataError("NewsData response must be a JSON object.")
    return payload


def fetch_and_append(
    *,
    output: Path,
    query: str | None,
    category: str,
    country: str,
    language: str,
    size: int,
    page: str | None,
    base_dir: Path,
) -> dict[str, Any]:
    api_key = require_env_value(ENV_KEY, base_dir=base_dir)

    params: dict[str, str] = {
        "apikey": api_key,
        "category": category,
        "country": country,
        "language": language,
        "size": str(size),
    }
    if query:
        params["q"] = query
    if page:
        params["page"] = page

    response = fetch_newsdata(params)
    if response.get("status") != "success":
        message = response.get("message") or response.get("results") or "Unknown error"
        raise NewsDataError(f"API error: {message}")

    results = response.get("results") or []
    if not isinstance(results, list):
        raise NewsDataError("API error: results payload is not a list")

    dump = load_dump(output)
    existing_keys = {
        article_key(item) for item in dump.get("articles", []) if isinstance(item, dict)
    }

    fetched_at = utc_now_iso()
    added = 0
    skipped = 0

    for raw_item in results:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        key = article_key(item)
        if key in existing_keys:
            skipped += 1
            continue

        item["fetched_at"] = fetched_at
        item["query_params"] = {
            "query": query,
            "category": category,
            "country": country,
            "language": language,
            "size": size,
            "page": page,
        }
        dump["articles"].append(item)
        existing_keys.add(key)
        added += 1

    dump["updated_at"] = fetched_at
    dump.setdefault("requests", []).append(
        {
            "fetched_at": fetched_at,
            "params": {
                "query": query,
                "category": category,
                "country": country,
                "language": language,
                "size": size,
                "page": page,
            },
            "status": response.get("status"),
            "total_results": response.get("totalResults"),
            "results_count": len(results),
            "next_page": response.get("nextPage"),
            "added": added,
            "skipped": skipped,
        }
    )

    write_json(output, dump)
    return {
        "output": str(output),
        "added": added,
        "skipped": skipped,
        "results_count": len(results),
        "total_results": response.get("totalResults"),
    }


def test_connection(*, base_dir: Path) -> dict[str, Any]:
    api_key = require_env_value(ENV_KEY, base_dir=base_dir)
    payload = fetch_newsdata(
        {
            "apikey": api_key,
            "category": "top",
            "country": "us",
            "language": "en",
            "size": "1",
        }
    )

    if payload.get("status") != "success":
        message = payload.get("message") or payload.get("results") or "Unknown error"
        raise NewsDataError(f"API error: {message}")

    results = payload.get("results") or []
    if not isinstance(results, list):
        raise NewsDataError("API error: results payload is not a list")

    top: dict[str, Any] | None = None
    if results and isinstance(results[0], dict):
        top = {
            "source": results[0].get("source_name") or results[0].get("source_id") or "unknown",
            "title": results[0].get("title") or "(no title)",
            "published": results[0].get("pubDate") or results[0].get("published_at") or "",
        }

    return {
        "status": payload.get("status"),
        "results": len(results),
        "total_results": payload.get("totalResults"),
        "top": top,
    }
