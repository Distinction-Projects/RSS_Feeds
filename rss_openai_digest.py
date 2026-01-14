#!/usr/bin/env python3
import argparse
import datetime
import hashlib
import html
import json
import os
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser

import feedparser

DEFAULT_CATALOG = os.path.join("feed_catalog", "rss_feeds.json")
DEFAULT_OUTPUT = os.path.join("data", "rss_openai_daily.json")
DEFAULT_ARCHIVE_DIR = os.path.join("data", "history")
OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
ENV_OPENAI_KEY = "OPENAI_API_KEY"
ENV_OPENAI_MODEL = "OPENAI_MODEL"
ENV_PATHS = [".env"]


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        if data:
            self._parts.append(data)

    def get_text(self):
        return "".join(self._parts)


def strip_html(value):
    if not value:
        return ""
    parser = _HTMLStripper()
    parser.feed(value)
    text = parser.get_text()
    return html.unescape(text).strip()


def compact_text(value, limit):
    if not value:
        return ""
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(limit - 3, 0)] + "..."


def utc_now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def read_env_file(path, key_name):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() == key_name:
                    return value.strip().strip("\"").strip("'")
    except OSError:
        return None
    return None


def load_env_value(key_name):
    value = os.environ.get(key_name)
    if value:
        return value

    script_dir = os.path.dirname(os.path.abspath(__file__))
    for rel_path in ENV_PATHS:
        path = os.path.join(script_dir, rel_path)
        value = read_env_file(path, key_name)
        if value:
            return value
    return None


def load_catalog(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Failed to load catalog {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def select_feeds(catalog, max_sources, feeds_per_source, source_ids):
    feeds = []
    selected_sources = 0
    sources = catalog.get("sources") or []
    for source in sources:
        if source_ids and source.get("id") not in source_ids:
            continue
        if selected_sources >= max_sources:
            break
        selected_sources += 1
        feed_list = source.get("feeds") or []
        for feed in feed_list[:feeds_per_source]:
            feeds.append(
                {
                    "source_id": source.get("id"),
                    "source_name": source.get("name"),
                    "feed_name": feed.get("name"),
                    "feed_url": feed.get("url"),
                    "topic_tags": feed.get("topic_tags") or [],
                }
            )
    return feeds


def item_id(source_id, link, title):
    base = (link or title or "").strip()
    digest = hashlib.sha1(f"{source_id}:{base}".encode("utf-8")).hexdigest()
    return digest[:12]


def fetch_feed_items(feed, max_items, timeout, user_agent):
    items = []
    request = urllib.request.Request(
        feed["feed_url"],
        headers={"User-Agent": user_agent},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content = response.read()

    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        exc = parsed.bozo_exception
        raise RuntimeError(f"Feed parse error: {exc}")

    for entry in parsed.entries[:max_items]:
        title = compact_text((entry.get("title") or "").strip(), 200)
        link = (entry.get("link") or "").strip()
        summary = compact_text(
            strip_html(entry.get("summary") or entry.get("description") or ""),
            500,
        )
        published = (entry.get("published") or entry.get("updated") or "").strip()
        items.append(
            {
                "id": item_id(feed["source_id"], link, title),
                "title": title,
                "link": link,
                "summary": summary,
                "published": published,
                "source_id": feed["source_id"],
                "source_name": feed["source_name"],
                "feed_name": feed["feed_name"],
                "feed_url": feed["feed_url"],
                "topic_tags": feed["topic_tags"],
            }
        )
    return items


def build_openai_messages(items):
    system = (
        "You summarize news items. For each item, return a short summary (max 1 sentence) "
        "and 3-6 topical tags. Use neutral language."
    )
    payload = {
        "items": [
            {
                "id": item["id"],
                "title": item["title"],
                "source": item["source_name"],
                "published": item["published"],
                "summary": item["summary"],
                "link": item["link"],
            }
            for item in items
        ]
    }
    user = (
        "Return JSON with an 'items' array. Each array item must include: "
        "id, summary, tags (array of short strings). Only return JSON.\n"
        + json.dumps(payload, ensure_ascii=True)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def extract_json(text):
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return ""


def call_openai(api_key, model, items, timeout):
    messages = build_openai_messages(items)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        OPENAI_ENDPOINT,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise RuntimeError(f"OpenAI request failed: {type(exc).__name__}: {exc}") from exc

    content = (
        result.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    raw = extract_json(content)
    if not raw:
        raise RuntimeError("OpenAI returned empty content.")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse OpenAI JSON: {exc}") from exc

    return parsed, result.get("id"), result.get("usage")


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch RSS feeds, summarize with OpenAI, write JSON.")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, help="Path to rss_feeds.json")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path")
    parser.add_argument("--archive-dir", default=DEFAULT_ARCHIVE_DIR, help="Archive directory")
    parser.add_argument("--no-archive", action="store_true", help="Disable archive copy")
    parser.add_argument("--max-sources", type=int, default=10, help="Max number of sources")
    parser.add_argument("--feeds-per-source", type=int, default=1, help="Feeds per source")
    parser.add_argument("--max-items-per-feed", type=int, default=3, help="Items per feed")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds")
    parser.add_argument("--source-ids", help="Comma-separated source IDs to include")
    parser.add_argument("--openai-model", help="OpenAI model override")
    parser.add_argument("--skip-openai", action="store_true", help="Skip OpenAI call")
    return parser.parse_args()


def main():
    args = parse_args()
    source_ids = [s.strip() for s in (args.source_ids or "").split(",") if s.strip()]

    catalog = load_catalog(args.catalog)
    feeds = select_feeds(catalog, args.max_sources, args.feeds_per_source, source_ids)

    if not feeds:
        print("No feeds selected; check catalog or filters.", file=sys.stderr)
        sys.exit(1)

    fetched_at = utc_now()
    items = []
    errors = []
    seen = set()
    user_agent = "RSS_Feeds/1.0 (+https://github.com)"

    for feed in feeds:
        try:
            feed_items = fetch_feed_items(
                feed, args.max_items_per_feed, args.timeout, user_agent
            )
        except Exception as exc:
            errors.append(
                {
                    "feed_url": feed["feed_url"],
                    "source_id": feed["source_id"],
                    "error": str(exc),
                }
            )
            continue

        for item in feed_items:
            dedupe_key = item["link"] or item["title"] or item["id"]
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            item["fetched_at"] = fetched_at
            items.append(item)

    output = {
        "schema_version": "1.0",
        "generated_at": fetched_at,
        "catalog_path": args.catalog,
        "request": {
            "max_sources": args.max_sources,
            "feeds_per_source": args.feeds_per_source,
            "max_items_per_feed": args.max_items_per_feed,
            "source_ids": source_ids,
        },
        "openai": None,
        "items": items,
        "errors": errors,
    }

    if items and not args.skip_openai:
        api_key = load_env_value(ENV_OPENAI_KEY)
        if not api_key:
            print(f"Missing {ENV_OPENAI_KEY}.", file=sys.stderr)
            sys.exit(1)
        model = args.openai_model or load_env_value(ENV_OPENAI_MODEL) or "gpt-4o-mini"

        parsed, response_id, usage = call_openai(api_key, model, items, args.timeout)
        mapping = {}
        for result_item in parsed.get("items") or []:
            item_key = result_item.get("id")
            if item_key:
                mapping[item_key] = result_item

        for item in items:
            ai = mapping.get(item["id"])
            if ai:
                item["ai_summary"] = ai.get("summary")
                item["ai_tags"] = ai.get("tags") or []

        output["openai"] = {
            "model": model,
            "response_id": response_id,
            "usage": usage,
        }

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=True)
        handle.write("\n")

    if not args.no_archive and args.archive_dir:
        date_stamp = fetched_at[:10]
        base_name = os.path.splitext(os.path.basename(args.output))[0]
        archive_name = f"{base_name}_{date_stamp}.json"
        archive_path = os.path.join(args.archive_dir, archive_name)
        os.makedirs(args.archive_dir, exist_ok=True)
        with open(archive_path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2, ensure_ascii=True)
            handle.write("\n")

    print(f"Wrote {len(items)} items to {args.output}")
    if errors:
        print(f"Encountered {len(errors)} feed errors.", file=sys.stderr)


if __name__ == "__main__":
    main()
