import unittest

from measure import summarize


class MeasureTest(unittest.TestCase):
    def test_summary_reports_latency_and_delivery_without_transcripts(self):
        report = summarize([
            {"provider": "local", "first_written_ms": 200, "final_delivery_ms": 300,
             "insertion": "typed", "text": "private"},
            {"provider": "local", "first_written_ms": 500, "final_delivery_ms": 900,
             "insertion": "clipboard_fallback"},
        ])
        self.assertEqual(report["first_written_ms"], {"n": 2, "p50": 200, "p95": 500})
        self.assertEqual(report["inserted"], 1)
        self.assertEqual(report["clipboard_fallback"], 1)
        self.assertNotIn("private", str(report))


if __name__ == "__main__":
    unittest.main()
