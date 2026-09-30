import json
import unittest
from pathlib import Path
from unittest.mock import patch

from saystride.store import Store


class DiagnosticsTest(unittest.TestCase):
    def test_diagnostics_returns_recent_privacy_safe_rows(self):
        store = Store.__new__(Store)
        saved = [{"id": "one", "provider": "deepgram", "transcript": "secret"},
                 {"id": "two", "provider": "local"}]
        with patch("saystride.store.APP_DIR", Path(".")), \
                patch("pathlib.Path.read_text", return_value=json.dumps(saved)):
            rows = store.diagnostics(limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider"], "deepgram")
        self.assertNotIn("transcript", rows[0])

    def test_diagnostics_store_metadata_without_transcript_or_audio(self):
        store = Store.__new__(Store)
        with patch("saystride.store.APP_DIR", Path(".")), patch("saystride.store._write") as write, \
                patch("pathlib.Path.read_text", side_effect=FileNotFoundError):
            store.record_diagnostic({
                "provider": "deepgram", "app": "WINWORD.EXE", "insertion": "clipboard",
                "error": None, "transcript": "private words", "audio": b"private audio",
            })
            rows = write.call_args.args[1]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider"], "deepgram")
        self.assertEqual(rows[0]["insertion"], "clipboard")
        self.assertNotIn("transcript", rows[0])
        self.assertNotIn("audio", rows[0])


if __name__ == "__main__":
    unittest.main()
