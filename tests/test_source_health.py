from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from rss_pipeline.cli import run_cli
from rss_pipeline.source_health import (
    build_source_health_trend_report,
    load_feed_audit_artifacts,
)


def _source_row(
    source_id: str,
    *,
    status: str,
    issue_count: int = 0,
    recommended_action: str = "keep",
) -> dict[str, object]:
    return {
        "source_id": source_id,
        "source_name": source_id.replace("-", " ").title(),
        "selected_feeds": 1,
        "feed_fetch_failed": 0,
        "raw_items": 5,
        "typical_newsfeed_items": max(5 - issue_count, 0),
        "newsfeed_excluded": issue_count,
        "quality_warn_items": 0,
        "quality_fail_items": 0,
        "missing_rss_content_items": 0,
        "accepted_content_type_filter_items": issue_count,
        "issue_count": issue_count,
        "issue_rate": round(issue_count / 5, 4),
        "status": status,
        "recommended_action": recommended_action,
        "issue_counts": [{"issue": "content_type_filter_accepted", "count": issue_count}]
        if issue_count
        else [],
    }


def _audit(
    *,
    generated_at: str,
    run_id: str,
    source_health: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": "warn",
        "run": {"id": run_id, "generated_at": generated_at},
        "source_health": source_health,
    }


class SourceHealthTests(unittest.TestCase):
    def test_build_source_health_trend_report_tracks_source_changes(self) -> None:
        old_audit = _audit(
            generated_at="2026-04-02T00:00:00Z",
            run_id="old",
            source_health=[
                _source_row("source-a", status="healthy"),
                _source_row(
                    "source-b",
                    status="watch",
                    issue_count=2,
                    recommended_action="review_source_mix",
                ),
            ],
        )
        current_audit = _audit(
            generated_at="2026-04-03T00:00:00Z",
            run_id="current",
            source_health=[
                _source_row(
                    "source-a",
                    status="watch",
                    issue_count=1,
                    recommended_action="review_source_mix",
                ),
                _source_row("source-b", status="healthy"),
                _source_row(
                    "source-c",
                    status="hold_candidate",
                    issue_count=3,
                    recommended_action="hold_or_disable_source",
                ),
            ],
        )

        report = build_source_health_trend_report(
            [
                {"path": "/tmp/rss_feed_audit_2026-04-02.json", "audit": old_audit},
                {"path": "/tmp/rss_feed_audit_2026-04-03.json", "audit": current_audit},
            ],
            limit=5,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["source_health_snapshot_count"], 2)
        self.assertEqual(
            report["latest_status_counts"],
            {"healthy": 1, "watch": 1, "hold_candidate": 1, "unknown": 0},
        )
        self.assertEqual(report["attention_summary"]["hold_candidates"], 1)
        self.assertEqual(report["attention_summary"]["degraded_sources"], 1)
        self.assertEqual(report["attention_summary"]["improved_sources"], 1)
        self.assertEqual(report["degraded_sources"][0]["source_id"], "source-a")
        self.assertEqual(report["improved_sources"][0]["source_id"], "source-b")
        self.assertEqual(report["hold_candidates"][0]["source_id"], "source-c")

    def test_source_health_cli_outputs_text_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history_dir = root / "history"
            history_dir.mkdir()
            current_path = root / "rss_feed_audit.json"
            history_path = history_dir / "rss_feed_audit_2026-04-02.json"
            history_path.write_text(
                json.dumps(
                    _audit(
                        generated_at="2026-04-02T00:00:00Z",
                        run_id="old",
                        source_health=[_source_row("source-a", status="healthy")],
                    )
                ),
                encoding="utf-8",
            )
            current_path.write_text(
                json.dumps(
                    _audit(
                        generated_at="2026-04-03T00:00:00Z",
                        run_id="current",
                        source_health=[
                            _source_row(
                                "source-a",
                                status="watch",
                                issue_count=1,
                                recommended_action="review_source_mix",
                            )
                        ],
                    )
                ),
                encoding="utf-8",
            )

            text_stdout = StringIO()
            with redirect_stdout(text_stdout):
                text_exit_code = run_cli(
                    [
                        "validate",
                        "source-health",
                        "--current",
                        str(current_path),
                        "--history-dir",
                        str(history_dir),
                    ]
                )

            json_stdout = StringIO()
            with redirect_stdout(json_stdout):
                json_exit_code = run_cli(
                    [
                        "validate",
                        "source-health",
                        "--current",
                        str(current_path),
                        "--history-dir",
                        str(history_dir),
                        "--json",
                    ]
                )

        self.assertEqual(text_exit_code, 0)
        self.assertIn("Source health trends:", text_stdout.getvalue())
        self.assertIn("Degraded sources:", text_stdout.getvalue())
        self.assertEqual(json_exit_code, 0)
        payload = json.loads(json_stdout.getvalue())
        self.assertEqual(payload["attention_summary"]["degraded_sources"], 1)

    def test_load_feed_audit_artifacts_reports_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_dir = Path(temp_dir) / "history"
            history_dir.mkdir()
            bad_path = history_dir / "rss_feed_audit_2026-04-02.json"
            bad_path.write_text("{not-json", encoding="utf-8")

            artifacts, load_errors = load_feed_audit_artifacts(history_dir=history_dir)

        self.assertEqual(artifacts, [])
        self.assertEqual(len(load_errors), 1)
        self.assertIn(str(bad_path), load_errors[0]["path"])


if __name__ == "__main__":
    unittest.main()
