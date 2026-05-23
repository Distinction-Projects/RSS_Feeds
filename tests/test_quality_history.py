from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from rss_pipeline.cli import run_cli
from rss_pipeline.quality_history import (
    archive_quality_review,
    build_quality_history_report,
    load_quality_review_artifacts,
)


def _review(
    *,
    generated_at: str,
    run_id: str,
    total_items: int,
    issue_items: int,
    unsupported_items: int,
    source_blocked_items: int,
    issue_counts: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    clean_items = max(total_items - issue_items, 0)
    return {
        "status": "warn" if issue_items else "pass",
        "generated_at": generated_at,
        "run_id": run_id,
        "total_items": total_items,
        "issue_item_count": issue_items,
        "status_counts": {"clean": clean_items, "warn": issue_items},
        "quality_gate_metrics": {
            "unknown_content_type_items": 0,
            "unsupported_content_type_items": unsupported_items,
            "source_blocked_items": source_blocked_items,
        },
        "issue_counts": issue_counts or [],
    }


class QualityHistoryTests(unittest.TestCase):
    def test_archive_quality_review_writes_dated_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history_dir = root / "history"
            review = _review(
                generated_at="2026-04-03T12:30:00Z",
                run_id="run-1",
                total_items=10,
                issue_items=1,
                unsupported_items=1,
                source_blocked_items=0,
            )

            archive_path = archive_quality_review(
                review,
                output_path=root / "rss_digest_quality_review.json",
                history_dir=history_dir,
            )

            self.assertEqual(archive_path.name, "rss_digest_quality_review_2026-04-03.json")
            self.assertEqual(json.loads(archive_path.read_text())["run_id"], "run-1")

    def test_build_quality_history_report_dedupes_current_and_reports_deltas(self) -> None:
        old_review = _review(
            generated_at="2026-04-02T12:30:00Z",
            run_id="run-old",
            total_items=12,
            issue_items=5,
            unsupported_items=3,
            source_blocked_items=2,
            issue_counts=[
                {"issue": "unsupported_content_type", "count": 3},
                {"issue": "source_blocked_403", "count": 2},
            ],
        )
        current_review = _review(
            generated_at="2026-04-03T12:30:00Z",
            run_id="run-current",
            total_items=16,
            issue_items=3,
            unsupported_items=2,
            source_blocked_items=1,
            issue_counts=[
                {"issue": "unsupported_content_type", "count": 2},
                {"issue": "source_blocked_403", "count": 1},
            ],
        )

        report = build_quality_history_report(
            [
                {"path": "/tmp/rss_digest_quality_review_2026-04-02.json", "review": old_review},
                {
                    "path": "/tmp/rss_digest_quality_review_2026-04-03.json",
                    "review": current_review,
                },
                {"path": "/tmp/rss_digest_quality_review.json", "review": current_review},
            ],
            limit=5,
        )

        self.assertEqual(report["snapshot_count"], 2)
        self.assertEqual(report["trend"], "improved")
        self.assertEqual(
            report["metric_deltas"]["issue_item_count"],
            {"previous": 5, "latest": 3, "delta": -2},
        )
        self.assertIn(
            {
                "issue": "source_blocked_403",
                "previous": 2,
                "latest": 1,
                "delta": -1,
            },
            report["issue_count_deltas"],
        )

    def test_quality_history_cli_outputs_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history_dir = root / "history"
            history_dir.mkdir()
            old_path = history_dir / "rss_digest_quality_review_2026-04-02.json"
            current_path = root / "rss_digest_quality_review.json"
            old_path.write_text(
                json.dumps(
                    _review(
                        generated_at="2026-04-02T12:30:00Z",
                        run_id="run-old",
                        total_items=12,
                        issue_items=2,
                        unsupported_items=1,
                        source_blocked_items=1,
                    )
                ),
                encoding="utf-8",
            )
            current_path.write_text(
                json.dumps(
                    _review(
                        generated_at="2026-04-03T12:30:00Z",
                        run_id="run-current",
                        total_items=14,
                        issue_items=4,
                        unsupported_items=2,
                        source_blocked_items=2,
                    )
                ),
                encoding="utf-8",
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = run_cli(
                    [
                        "validate",
                        "quality-history",
                        "--current",
                        str(current_path),
                        "--history-dir",
                        str(history_dir),
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["snapshot_count"], 2)
        self.assertEqual(payload["trend"], "worse")
        self.assertEqual(payload["metric_deltas"]["total_items"]["delta"], 2)

    def test_load_quality_review_artifacts_reports_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_dir = Path(temp_dir) / "history"
            history_dir.mkdir()
            bad_path = history_dir / "rss_digest_quality_review_2026-04-02.json"
            bad_path.write_text("{not-json", encoding="utf-8")

            artifacts, load_errors = load_quality_review_artifacts(history_dir=history_dir)

        self.assertEqual(artifacts, [])
        self.assertEqual(len(load_errors), 1)
        self.assertIn(str(bad_path), load_errors[0]["path"])


if __name__ == "__main__":
    unittest.main()
