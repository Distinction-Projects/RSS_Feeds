from __future__ import annotations

import unittest
from urllib.error import HTTPError, URLError

from rss_pipeline.failure_taxonomy import (
    classification_from_scrape_audit,
    classify_scrape_failure,
)


class FailureTaxonomyTests(unittest.TestCase):
    def test_classifies_http_error_403_as_source_blocked(self) -> None:
        classification = classify_scrape_failure(
            HTTPError(
                url="https://example.com/article",
                code=403,
                msg="Forbidden",
                hdrs={},
                fp=None,
            )
        )

        self.assertEqual(classification.code, "source_blocked_403")
        self.assertEqual(classification.category, "source_blocked")
        self.assertEqual(classification.http_status, 403)
        self.assertFalse(classification.retryable)

    def test_classifies_legacy_http_403_error_string(self) -> None:
        classification = classify_scrape_failure("HTTP Error 403: Forbidden")

        self.assertEqual(classification.code, "source_blocked_403")
        self.assertEqual(classification.http_status, 403)

    def test_classifies_transient_failures(self) -> None:
        timeout = classify_scrape_failure(TimeoutError("timed out"))
        network = classify_scrape_failure(URLError("temporary lookup failure"))

        self.assertEqual(timeout.code, "fetch_timeout")
        self.assertTrue(timeout.retryable)
        self.assertEqual(network.code, "network_error")
        self.assertTrue(network.retryable)

    def test_rehydrates_classification_from_scrape_audit(self) -> None:
        classification = classification_from_scrape_audit(
            {
                "status": "failed",
                "reason": "source_blocked_403",
                "error": "HTTP Error 403: Forbidden",
            }
        )

        self.assertEqual(classification.code, "source_blocked_403")
        self.assertEqual(classification.category, "source_blocked")
        self.assertEqual(classification.raw_error, "HTTP Error 403: Forbidden")


if __name__ == "__main__":
    unittest.main()
