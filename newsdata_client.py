#!/usr/bin/env python3
import argparse
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

BASE_URL = "https://newsdata.io/api/1/news"
DEFAULT_OUTPUT = os.path.join("data", "newsdata_dump.json")
ENV_KEY = "NEWSDATA_API_KEY"


def utc_now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def read_env_file(path):
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
                if key.strip() == ENV_KEY:
                    return value.strip().strip("\"").strip("'")
    except OSError:
        return None
    return None


def load_api_key():
    key = os.environ.get(ENV_KEY)
    if key:
        return key

    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidate_paths = [
        os.path.join(script_dir, ".env"),
        os.path.join(script_dir, "RSS_Feeds", ".env"),
    ]

    for path in candidate_paths:
        key = read_env_file(path)
        if key:
            return key

    return None


def load_dump(path):
    if not os.path.exists(path):
        return {
            "schema_version": "1.0",
            "updated_at": None,
            "articles": [],
            "requests": [],
        }

    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read().strip()
            if not content:
                return {
                    "schema_version": "1.0",
                    "updated_at": None,
                    "articles": [],
                    "requests": [],
                }
            data = json.loads(content)
    except (OSError, json.JSONDecodeError):
        print(f"Failed to read or parse {path}", file=sys.stderr)
        sys.exit(1)

    if isinstance(data, list):
        return {
            "schema_version": "1.0",
            "updated_at": None,
            "articles": data,
            "requests": [],
        }

    if not isinstance(data, dict):
        print(f"Unexpected JSON format in {path}", file=sys.stderr)
        sys.exit(1)

    data.setdefault("schema_version", "1.0")
    data.setdefault("updated_at", None)
    data.setdefault("articles", [])
    data.setdefault("requests", [])

    if not isinstance(data["articles"], list):
        data["articles"] = []
    if not isinstance(data["requests"], list):
        data["requests"] = []

    return data


def save_dump(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def article_key(item):
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


def fetch_newsdata(params):
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}?{query}"
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.load(response)


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch NewsData and append to a JSON dump.")
    parser.add_argument("--query", dest="query", help="Keyword query (q parameter)")
    parser.add_argument("--category", default="top", help="News category (default: top)")
    parser.add_argument("--country", default="us", help="Country code (default: us)")
    parser.add_argument("--language", default="en", help="Language code (default: en)")
    parser.add_argument("--size", default=1, type=int, help="Number of items to fetch (default: 1)")
    parser.add_argument("--page", help="Pagination token (nextPage)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path")
    return parser.parse_args()


def main():
    args = parse_args()
    api_key = load_api_key()
    if not api_key:
        print(f"Missing {ENV_KEY}. Set it in the environment or a .env file.", file=sys.stderr)
        sys.exit(1)

    params = {
        "apikey": api_key,
        "category": args.category,
        "country": args.country,
        "language": args.language,
        "size": str(args.size),
    }
    if args.query:
        params["q"] = args.query
    if args.page:
        params["page"] = args.page

    try:
        response = fetch_newsdata(params)
    except Exception as exc:
        print(f"Request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

    if response.get("status") != "success":
        message = response.get("message") or response.get("results") or "Unknown error"
        print(f"API error: {message}", file=sys.stderr)
        sys.exit(1)

    results = response.get("results") or []
    dump = load_dump(args.output)

    existing_keys = set()
    for item in dump.get("articles", []):
        existing_keys.add(article_key(item))

    fetched_at = utc_now()
    added = 0
    skipped = 0

    for item in results:
        key = article_key(item)
        if key in existing_keys:
            skipped += 1
            continue
        item["fetched_at"] = fetched_at
        item["query_params"] = {
            "query": args.query,
            "category": args.category,
            "country": args.country,
            "language": args.language,
            "size": args.size,
            "page": args.page,
        }
        dump["articles"].append(item)
        existing_keys.add(key)
        added += 1

    dump["updated_at"] = fetched_at
    dump.setdefault("requests", []).append(
        {
            "fetched_at": fetched_at,
            "params": {
                "query": args.query,
                "category": args.category,
                "country": args.country,
                "language": args.language,
                "size": args.size,
                "page": args.page,
            },
            "status": response.get("status"),
            "total_results": response.get("totalResults"),
            "results_count": len(results),
            "next_page": response.get("nextPage"),
            "added": added,
            "skipped": skipped,
        }
    )

    save_dump(args.output, dump)

    print(f"Saved {added} new article(s), skipped {skipped}. Output: {args.output}")


if __name__ == "__main__":
    main()
