"""Install the latest official Windows Vulkan llama.cpp binary into tools/llama."""
import hashlib
import re
from pathlib import Path
from zipfile import ZipFile

import requests

root = Path(__file__).resolve().parent
releases = requests.get("https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10", timeout=20)
releases.raise_for_status()
asset = next(item for release in releases.json() for item in release["assets"]
             if item["name"].endswith("bin-win-vulkan-x64.zip"))
target = root / "tools" / "llama"
target.mkdir(parents=True, exist_ok=True)
archive = target / "llama.zip"
with requests.get(asset["browser_download_url"], stream=True, timeout=(15, 90)) as response:
    response.raise_for_status()
    with archive.open("wb") as file:
        for chunk in response.iter_content(1024 * 1024):
            file.write(chunk)
expected = asset.get("digest", "")
if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected):
    raise ValueError("llama.cpp release has no valid SHA-256 digest")
with archive.open("rb") as file:
    actual = hashlib.file_digest(file, "sha256").hexdigest()
if actual != expected[7:]:
    raise ValueError("llama.cpp download failed SHA-256 verification")
with ZipFile(archive) as zipped:
    for member in zipped.infolist():
        if not (target / member.filename).resolve().is_relative_to(target.resolve()):
            raise ValueError("Unsafe llama.cpp archive")
    zipped.extractall(target)
archive.unlink()
print("Installed", next(target.rglob("llama-server.exe")))
