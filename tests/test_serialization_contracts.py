from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from lens import Lens, Score
from load_experiment import ExperimentData, NewsItem, load_experiment

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Fixture at {path} must contain a top-level object.")
    return payload


class SerializationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.canonical_digest = _read_json(FIXTURES_DIR / "canonical_digest.json")
        self.legacy_digest = _read_json(FIXTURES_DIR / "legacy_digest.json")
        self.valid_lens = _read_json(FIXTURES_DIR / "valid_lens.json")
        self.invalid_score = _read_json(FIXTURES_DIR / "invalid_score.json")

    def test_newsitem_dict_roundtrip_preserves_key_fields(self) -> None:
        item_payload = self.canonical_digest["items"][0]
        item = NewsItem.from_dict(item_payload)
        reparsed = NewsItem.from_dict(item.to_dict())
        self.assertEqual(item.id, reparsed.id)
        self.assertEqual(item.ai_summary, reparsed.ai_summary)
        original_lead = item.scraped.lead_paragraph if item.scraped else None
        reparsed_lead = reparsed.scraped.lead_paragraph if reparsed.scraped else None
        self.assertEqual(original_lead, reparsed_lead)
        original_body = item.scraped.body_text if item.scraped else None
        reparsed_body = reparsed.scraped.body_text if reparsed.scraped else None
        self.assertEqual(original_body, reparsed_body)
        self.assertEqual(item.scrape_error, reparsed.scrape_error)

    def test_newsitem_json_roundtrip_compat(self) -> None:
        item_payload = self.canonical_digest["items"][0]
        item = NewsItem.from_dict(item_payload)
        reparsed = NewsItem.from_json(item.to_json())
        self.assertEqual(item.id, reparsed.id)
        self.assertEqual(item.title, reparsed.title)
        self.assertEqual(item.ai_tags, reparsed.ai_tags)

    def test_newsitem_strict_rejects_malformed_payload(self) -> None:
        item_payload = dict(self.canonical_digest["items"][0])
        item_payload.pop("id", None)
        with self.assertRaises(ValueError):
            NewsItem.from_json(json.dumps(item_payload), strict=True)

    def test_experimentdata_roundtrip_preserves_summary(self) -> None:
        data = ExperimentData.from_payload(self.canonical_digest)
        reparsed = ExperimentData.from_json(data.to_json(), strict=True)
        self.assertEqual(data.summary.total_items, reparsed.summary.total_items)
        self.assertEqual(data.summary.total_sources, reparsed.summary.total_sources)
        self.assertEqual(data.summary.total_errors, reparsed.summary.total_errors)

    def test_legacy_digest_parses_in_compat_mode(self) -> None:
        data = ExperimentData.from_json(json.dumps(self.legacy_digest))
        self.assertEqual(1, data.summary.total_items)
        self.assertEqual(1, data.summary.total_sources)
        self.assertEqual(1, data.summary.total_errors)

    def test_lens_roundtrip_preserves_rubrics(self) -> None:
        lens = Lens.from_dict(self.valid_lens)
        reparsed = Lens.from_json(lens.to_json(), strict=True)
        self.assertEqual(lens.name, reparsed.name)
        self.assertEqual(len(lens.rubrics), len(reparsed.rubrics))
        self.assertEqual(lens.rubrics[0].name, reparsed.rubrics[0].name)

    def test_score_invariant_rejects_invalid_fixture(self) -> None:
        with self.assertRaises(ValueError):
            Score.from_dict(self.invalid_score)

    def test_current_digest_loads_without_regression(self) -> None:
        digest_path = REPO_ROOT / "data" / "rss_openai_daily.json"
        data = load_experiment(digest_path)
        self.assertEqual(len(data.items), data.summary.total_items)
        self.assertGreaterEqual(data.summary.total_sources, 0)


if __name__ == "__main__":
    unittest.main()
