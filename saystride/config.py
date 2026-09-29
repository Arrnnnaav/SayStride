"""Windows settings and local paths. The Mac project remains independent in ../macos."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
DATA_MARKER = ROOT / "data-dir.txt"
selected_dir = os.environ.get("SAYSTRIDE_DATA_DIR") or (DATA_MARKER.read_text(encoding="utf-8").strip() if DATA_MARKER.exists() else "")
APP_DIR = Path(selected_dir) if selected_dir else Path(os.environ.get("APPDATA", Path.home())) / "SayStride"
MODELS_DIR = APP_DIR / "models"
CLIPS_DIR = APP_DIR / "clips"
MEETINGS_DIR = APP_DIR / "meetings"

TONES = {
    "supercasual": "Tone: casual text to a friend. Lowercase sentence starts and i, keep names capitalized. Light punctuation, no final full stop, keep questions, apostrophes and laughter. Keep the speaker's wording.",
    "casual": "Tone: casual message to a friend or colleague. Lowercase sentence starts and i, keep names capitalized. Normal commas and full stops. Keep the speaker's wording and laughter.",
    "professional": "Tone: message to a colleague or client. Normal capitalization and punctuation, complete sentences, no slang. Keep the speaker's phrasing; only fix grammar and remove fillers.",
    "verbatim": "",
}

DEFAULT = {
    "asr_backend": "parakeet",  # falls back to faster-whisper if the model is missing
    "asr_version": "v2",
    "whisper_model": "base",
    "microphone": None,
    "tap_threshold": 0.5,
    "hotkey": "F8",
    "toggle_hotkey": "F9",
    "default_tones": {"tone": "supercasual", "alt": "professional"},
    "app_tones": {"Code.exe": {"tone": "verbatim", "alt": "casual"},
                  "OUTLOOK.EXE": {"tone": "professional", "alt": "casual"},
                  "WhatsApp.exe": {"tone": "supercasual", "alt": "professional"}},
    "tones": TONES,
    "dictionary": ["SayStride: say stride, say stryde"],
    "learned_signals": [],
    "memory": "",
    "keep_clips": True,
    "overlay": True,
    "sounds": True,
    "field_context": True,
    "live_text": True,
    "live_window_seconds": 8,
    "live_decode_interval": 0.45,
    "live_pad_seconds": 0.5,
    "live_beam_size": 3,
    "live_text_excluded": ["WindowsTerminal.exe", "Code.exe", "cmd.exe", "powershell.exe"],
    "cleanup_mode": "fast",  # deterministic rules; quality uses the configured LLM
    "window_at_launch": True,
    "llama_server_path": str(ROOT / "tools" / "llama" / "llama-server.exe") if (ROOT / "tools" / "llama" / "llama-server.exe").exists() else "llama-server.exe",
    "llama_port": 8765,
    "llm_backend": "auto",  # llama-server with GGUF, otherwise local Ollama
    "ollama_model": "qwen3:4b-instruct",
    "model": "Qwen3.5-4B-Q4_K_M.gguf",
    "notes_model": "",
    "cloud_enabled": False,
    "cloud_dictation": "long",  # off, long, all
    "cloud_meetings": True,
    "cloud_api_key": "",
    "cloud_asr_model": "whisper-large-v3-turbo",
    "cloud_llm_model": "qwen/qwen3.8-27b",
    "dictation_provider": "local",  # local or deepgram; meetings remain on their existing path
    "deepgram_model": "nova-3",
    "deepgram_language": "en",
    "deepgram_keyterms": True,
    "meeting_engine": "auto",
    "meeting_detect": True,
    "chinese_script": "traditional",
}


def load() -> dict:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    for path in (MODELS_DIR, CLIPS_DIR, MEETINGS_DIR):
        path.mkdir(exist_ok=True)
    path = APP_DIR / "config.json"
    if not path.exists():
        save(DEFAULT)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A malformed config must never be overwritten by the fallback defaults.
        return json.loads(json.dumps(DEFAULT))
    config = json.loads(json.dumps(DEFAULT))
    config.update(saved)
    config["tones"] = {**TONES, **saved.get("tones", {})}
    return config


def deepgram_key() -> str:
    """Read the API key without copying it into the persisted app settings."""
    value = os.environ.get("DEEPGRAM_API_KEY") or os.environ.get("DEEPGRAM")
    if value:
        return value.strip()
    paths = [APP_DIR / ".env", ROOT.parent / ".env", ROOT / ".env",
             APP_DIR.parent / "SayStride" / ".env"]
    if getattr(sys, "frozen", False) and len(ROOT.parents) > 2:
        paths.append(ROOT.parents[2] / ".env")  # source project beside windows/dist/SayStride
    for path in paths:
        try:
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                name, sep, secret = line.partition("=")
                if sep and name.strip() in {"DEEPGRAM", "DEEPGRAM_API_KEY"}:
                    return secret.strip().strip('"').strip("'")
        except FileNotFoundError:
            continue
    return ""


def save(config: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    path = APP_DIR / "config.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
