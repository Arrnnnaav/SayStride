import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from saystride.models import download_model


class DownloadResponse:
    status_code = 200
    headers = {"Content-Length": "10"}

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def iter_content(self, _):
        yield self.data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class ModelIntegrityTest(unittest.TestCase):
    def test_corrupt_and_existing_model_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            expected = hashlib.sha256(b"valid model").hexdigest()
            with patch("saystride.models.MODELS_DIR", Path(temporary)), \
                 patch.dict("saystride.models.MODEL_SHA256", {"qwen": expected}), \
                 patch("saystride.models.requests.get", return_value=DownloadResponse(b"tampered")):
                with self.assertRaisesRegex(ValueError, "SHA-256 verification"):
                    download_model("qwen")
                self.assertFalse((Path(temporary) / "Qwen3.5-4B-Q4_K_M.gguf").exists())
            with patch("saystride.models.MODELS_DIR", Path(temporary)), \
                 patch.dict("saystride.models.MODEL_SHA256", {"qwen": expected}), \
                 patch("saystride.models.requests.get", return_value=DownloadResponse(b"valid model")):
                model = download_model("qwen")
                self.assertEqual(model.read_bytes(), b"valid model")
                model.write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "Existing Qwen model"):
                    download_model("qwen")


if __name__ == "__main__":
    unittest.main()
