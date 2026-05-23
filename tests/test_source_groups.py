from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SourceGroupsTests(unittest.TestCase):
    def test_curated_groups_reference_enabled_catalog_sources(self) -> None:
        catalog = json.loads((ROOT / "feed_catalog/rss_feeds.json").read_text(encoding="utf-8"))
        groups = json.loads((ROOT / "feed_catalog/source_groups.json").read_text(encoding="utf-8"))

        enabled_source_ids = {
            source["id"]
            for source in catalog["sources"]
            if isinstance(source, dict) and source.get("enabled") is True
        }

        self.assertEqual(groups["schema_version"], "1.0")
        self.assertGreaterEqual(len(groups["groups"]), 6)

        for group in groups["groups"]:
            primary_source_ids = group.get("primary_source_ids") or []
            alternate_source_ids = group.get("alternate_source_ids") or []

            self.assertEqual(
                len(primary_source_ids),
                2,
                f"{group.get('id')} should pick exactly two primary sources",
            )
            self.assertEqual(
                len(primary_source_ids),
                len(set(primary_source_ids)),
                f"{group.get('id')} primary sources should be unique",
            )

            referenced_source_ids = set(primary_source_ids) | set(alternate_source_ids)
            missing_source_ids = sorted(referenced_source_ids - enabled_source_ids)
            self.assertEqual(missing_source_ids, [], f"{group.get('id')} references disabled or missing sources")


if __name__ == "__main__":
    unittest.main()
