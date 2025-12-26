#!/usr/bin/env python3
import json
import os
import sys
import urllib.parse
import urllib.request

BASE_URL = "https://newsdata.io/api/1/news"
ENV_KEY = "NEWSDATA_API_KEY"


def main():
    api_key = os.environ.get(ENV_KEY)
    if not api_key:
        print(f"Missing {ENV_KEY}. Add it as an env var or repo secret.", file=sys.stderr)
        sys.exit(1)

    params = {
        "apikey": api_key,
        "category": "top",
        "country": "us",
        "language": "en",
        "size": "1",
    }
    url = BASE_URL + "?" + urllib.parse.urlencode(params)

    print("NewsData test run (size=1, category=top, country=us, language=en)")

    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            data = json.load(response)
    except Exception as exc:
        print(f"Request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

    status = data.get("status")
    if status != "success":
        message = data.get("message") or data.get("results") or "Unknown error"
        print(f"API error: {message}", file=sys.stderr)
        sys.exit(1)

    results = data.get("results") or []
    total = data.get("totalResults")

    print(f"Status: {status} | Results: {len(results)} | Total: {total}")

    if not results:
        print("No results returned.")
        return

    item = results[0]
    source = item.get("source_name") or item.get("source_id") or "unknown"
    title = item.get("title") or "(no title)"
    published = item.get("pubDate") or item.get("published_at") or ""

    print(f"Top: {source} | {title}")
    if published:
        print(f"Published: {published}")


if __name__ == "__main__":
    main()
