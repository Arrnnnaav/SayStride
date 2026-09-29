"""A mismatched release digest must stop before installing an executable."""
import hashlib
import io
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile


class Response:
    def __init__(self, payload, releases=None):
        self.payload = payload
        self.releases = releases

    def raise_for_status(self):
        pass

    def json(self):
        return self.releases

    def iter_content(self, _):
        yield self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class DownloadIntegrityTest(unittest.TestCase):
    def test_release_digest_controls_extraction(self):
        source = Path(__file__).resolve().parents[1] / "download_llama.py"
        if not source.exists():
            self.skipTest("download_llama.py is a source-only installer")
        data = io.BytesIO()
        with ZipFile(data, "w") as zipped:
            zipped.writestr("llama-server.exe", b"test executable")
        payload = data.getvalue()
        correct = "sha256:" + hashlib.sha256(payload).hexdigest()

        for digest, accepted in ((correct, True), ("sha256:" + "0" * 64, False)):
            with self.subTest(accepted=accepted), tempfile.TemporaryDirectory() as temporary:
                script = Path(temporary) / "download_llama.py"
                script.write_bytes(source.read_bytes())
                asset = {"name": "test-bin-win-vulkan-x64.zip", "browser_download_url": "https://example.test/llama.zip",
                         "digest": digest}
                releases = [{"assets": [asset]}]
                def get(url, **_):
                    return Response(b"", releases) if "api.github.com" in url else Response(payload)
                with patch.dict(sys.modules, {"requests": SimpleNamespace(get=get)}):
                    if accepted:
                        runpy.run_path(str(script))
                    else:
                        with self.assertRaisesRegex(ValueError, "SHA-256 verification"):
                            runpy.run_path(str(script))
                installed = Path(temporary) / "tools" / "llama" / "llama-server.exe"
                self.assertEqual(installed.exists(), accepted)


if __name__ == "__main__":
    unittest.main()
