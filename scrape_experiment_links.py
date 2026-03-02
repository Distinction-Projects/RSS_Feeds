from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from load_experiment import ExperimentData, ScrapedArticle, load_experiment, load_experiments

REQUEST_TIMEOUT_SECONDS = 12
MAX_HTML_BYTES = 1_000_000
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_SLEEP_SECONDS = 0.25
MAX_KEYWORDS = 12
MIN_KEYWORD_LENGTH = 4
MAX_PARAGRAPHS_FOR_KEYWORDS = 12
MIN_PARAGRAPH_CHARS = 40

TITLE_META_KEYS: tuple[str, ...] = ("og:title", "twitter:title", "headline")
DESCRIPTION_META_KEYS: tuple[str, ...] = (
    "description",
    "og:description",
    "twitter:description",
)
AUTHOR_META_KEYS: tuple[str, ...] = (
    "author",
    "article:author",
    "parsely-author",
    "dc.creator",
)
PUBLISHED_META_KEYS: tuple[str, ...] = (
    "article:published_time",
    "og:published_time",
    "publishdate",
    "pubdate",
    "date",
    "dc.date",
)
CANONICAL_META_KEYS: tuple[str, ...] = ("og:url",)
LANG_META_KEYS: tuple[str, ...] = ("og:locale",)

STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "also",
    "among",
    "because",
    "before",
    "being",
    "between",
    "could",
    "first",
    "found",
    "from",
    "have",
    "into",
    "just",
    "more",
    "most",
    "news",
    "other",
    "over",
    "said",
    "same",
    "some",
    "that",
    "their",
    "there",
    "these",
    "they",
    "this",
    "those",
    "under",
    "very",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "would",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _as_datetime_optional(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None


def _first_non_empty(values: list[str | None]) -> str | None:
    for value in values:
        if value is None:
            continue
        text = _clean_text(value)
        if text:
            return text
    return None


def _pick_meta(meta: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = meta.get(key)
        if value:
            return _clean_text(value)
    return None


def _extract_meta_map(soup: BeautifulSoup) -> dict[str, str]:
    meta: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        content = _clean_text(tag.get("content", ""))
        if not content:
            continue

        key = _clean_text(tag.get("property", "")).lower()
        if not key:
            key = _clean_text(tag.get("name", "")).lower()
        if not key:
            key = _clean_text(tag.get("itemprop", "")).lower()
        if key and key not in meta:
            meta[key] = content
    return meta


def _extract_canonical_url(soup: BeautifulSoup) -> str | None:
    canonical = soup.find("link", rel=lambda value: value and "canonical" in str(value).lower())
    if canonical and canonical.has_attr("href"):
        return _clean_text(str(canonical["href"]))
    return None


def _extract_language(soup: BeautifulSoup, meta: dict[str, str]) -> str | None:
    html_tag = soup.find("html")
    if html_tag and html_tag.has_attr("lang"):
        lang = _clean_text(str(html_tag["lang"]))
        if lang:
            return lang
    return _pick_meta(meta, LANG_META_KEYS)


def _extract_title(soup: BeautifulSoup, meta: dict[str, str]) -> str | None:
    return _first_non_empty(
        [
            _pick_meta(meta, TITLE_META_KEYS),
            _clean_text(soup.title.get_text(" ", strip=True)) if soup.title else None,
        ]
    )


def _extract_h1(soup: BeautifulSoup) -> str | None:
    h1 = soup.find("h1")
    if not h1:
        return None
    return _clean_text(h1.get_text(" ", strip=True))


def _extract_paragraphs(soup: BeautifulSoup) -> list[str]:
    paragraphs: list[str] = []
    for paragraph in soup.find_all("p"):
        text = _clean_text(paragraph.get_text(" ", strip=True))
        if text:
            paragraphs.append(text)
    return paragraphs


def _extract_keywords(title: str | None, paragraphs: list[str]) -> list[str]:
    text_parts: list[str] = []
    if title:
        text_parts.append(title)
    text_parts.extend(paragraphs[:MAX_PARAGRAPHS_FOR_KEYWORDS])
    corpus = " ".join(text_parts).lower()

    words = re.findall(r"[a-z][a-z'-]+", corpus)
    counts: Counter[str] = Counter()
    for word in words:
        normalized = word.strip("'")
        if len(normalized) < MIN_KEYWORD_LENGTH:
            continue
        if normalized in STOPWORDS:
            continue
        counts[normalized] += 1

    return [word for word, _ in counts.most_common(MAX_KEYWORDS)]


def _extract_lead_paragraph(paragraphs: list[str]) -> str | None:
    for paragraph in paragraphs:
        if len(paragraph) >= MIN_PARAGRAPH_CHARS:
            return paragraph
    return paragraphs[0] if paragraphs else None


def _fetch_html(url: str, timeout_seconds: float, user_agent: str) -> tuple[str, int | None, str | None, str]:
    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
        },
    )

    with urlopen(request, timeout=timeout_seconds) as response:
        final_url = response.geturl()
        status_code = getattr(response, "status", None)
        content_type_header = response.headers.get("Content-Type", "")
        content_type = content_type_header.split(";")[0].strip().lower() or None

        raw = response.read(MAX_HTML_BYTES + 1)
        if len(raw) > MAX_HTML_BYTES:
            raw = raw[:MAX_HTML_BYTES]

        charset = response.headers.get_content_charset() or "utf-8"
        try:
            html = raw.decode(charset, errors="replace")
        except LookupError:
            html = raw.decode("utf-8", errors="replace")

    return final_url, status_code, content_type, html


def scrape_article(url: str, timeout_seconds: float, user_agent: str) -> ScrapedArticle:
    final_url, status_code, content_type, html = _fetch_html(url, timeout_seconds, user_agent)
    if content_type and "html" not in content_type:
        raise ValueError(f"unsupported content type: {content_type}")

    soup = BeautifulSoup(html, "html.parser")
    meta = _extract_meta_map(soup)
    title = _extract_title(soup, meta)
    description = _pick_meta(meta, DESCRIPTION_META_KEYS)
    author = _pick_meta(meta, AUTHOR_META_KEYS)
    published_at = _as_datetime_optional(_pick_meta(meta, PUBLISHED_META_KEYS))
    canonical_url = _first_non_empty([_extract_canonical_url(soup), _pick_meta(meta, CANONICAL_META_KEYS)])
    language = _extract_language(soup, meta)

    paragraphs = _extract_paragraphs(soup)
    lead_paragraph = _extract_lead_paragraph(paragraphs)
    word_count = len(re.findall(r"\b\w+\b", " ".join(paragraphs)))
    top_keywords = _extract_keywords(title, paragraphs)

    return ScrapedArticle(
        fetched_at=_now_utc(),
        final_url=final_url,
        status_code=status_code,
        content_type=content_type,
        title=title,
        description=description,
        author=author,
        published_at=published_at,
        canonical_url=canonical_url,
        language=language,
        h1=_extract_h1(soup),
        lead_paragraph=lead_paragraph,
        paragraph_count=len(paragraphs),
        word_count=word_count,
        top_keywords=top_keywords,
    )


def scrape_experiment_data(
    data: ExperimentData,
    limit: int | None,
    sleep_seconds: float,
    timeout_seconds: float,
    user_agent: str,
    force_rescrape: bool,
) -> dict[str, int]:
    attempts = 0
    success = 0
    failed = 0
    skipped = 0

    for item in data.items:
        if limit is not None and attempts >= limit:
            break

        if not item.link:
            item.scraped = None
            item.scrape_error = "missing link"
            failed += 1
            attempts += 1
            continue

        if item.scraped is not None and not force_rescrape:
            skipped += 1
            continue

        try:
            item.scraped = scrape_article(item.link, timeout_seconds=timeout_seconds, user_agent=user_agent)
            item.scrape_error = None
            success += 1
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            item.scraped = None
            item.scrape_error = str(exc)
            failed += 1
        except Exception as exc:  # keep scraper resilient across diverse sites
            item.scraped = None
            item.scrape_error = f"unexpected error: {exc}"
            failed += 1

        attempts += 1
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return {"attempts": attempts, "success": success, "failed": failed, "skipped": skipped}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scrape article pages from experiment items and store extracted page data "
            "inside each item's `scraped` dataclass fields."
        )
    )
    parser.add_argument(
        "--input",
        default="data/rss_openai_daily.json",
        help="Input experiment JSON file, directory, or glob pattern.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for a single input file. Defaults to <input_stem>_scraped.json.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Output directory when processing multiple input files. "
            "Defaults to each input file's directory."
        ),
    )
    parser.add_argument(
        "--output-suffix",
        default="_scraped",
        help="Suffix for output filenames in batch mode.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max number of links to scrape.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help="Delay between requests to reduce request rate.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=REQUEST_TIMEOUT_SECONDS,
        help="HTTP timeout per request.",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="HTTP User-Agent header.",
    )
    parser.add_argument(
        "--force-rescrape",
        action="store_true",
        help="Scrape items even if `scraped` data already exists.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    input_entries = load_experiments([args.input])
    if not input_entries:
        raise SystemExit("No matching input files found.")

    is_single = len(input_entries) == 1

    def resolve_output_path(input_path: Path) -> Path:
        if is_single and args.output:
            return Path(args.output)
        output_dir = Path(args.output_dir) if args.output_dir else input_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / f"{input_path.stem}{args.output_suffix}.json"

    for input_path, data in input_entries:
        output_path = resolve_output_path(input_path)
        stats = scrape_experiment_data(
            data,
            limit=args.limit,
            sleep_seconds=args.sleep_seconds,
            timeout_seconds=args.timeout_seconds,
            user_agent=args.user_agent,
            force_rescrape=args.force_rescrape,
        )

        payload = data.to_dict()
        payload["scrape"] = {
            "run_at": _now_utc().isoformat(),
            "input": str(input_path),
            "output": str(output_path),
            "attempts": stats["attempts"],
            "success": stats["success"],
            "failed": stats["failed"],
            "skipped": stats["skipped"],
            "timeout_seconds": args.timeout_seconds,
            "sleep_seconds": args.sleep_seconds,
        }

        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(
            f"Wrote {output_path} | attempts={stats['attempts']} "
            f"success={stats['success']} failed={stats['failed']} skipped={stats['skipped']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
