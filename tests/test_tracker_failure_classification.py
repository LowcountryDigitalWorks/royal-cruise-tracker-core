from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.classify_tracker_failure import classify


class TrackerFailureClassificationTests(unittest.TestCase):
    def test_http_400(self) -> None:
        self.assertEqual(classify("400 Client Error: Bad Request for url: [redacted]"), "royal_http_400")

    def test_http_403(self) -> None:
        self.assertEqual(classify("HTTP Error 403 Access Denied"), "royal_http_403")

    def test_rate_limit(self) -> None:
        self.assertEqual(classify("429 Client Error: Too Many Requests"), "royal_rate_limited")

    def test_server_error(self) -> None:
        self.assertEqual(classify("HTTP Error 503 from Royal API"), "royal_server_error")

    def test_network_failure(self) -> None:
        self.assertEqual(classify("Connection reset while request timed out"), "royal_network_failure")

    def test_unknown_failure_stays_generic(self) -> None:
        self.assertEqual(classify("checker exited unexpectedly"), "royal_checker_failure")


if __name__ == "__main__":
    unittest.main()
