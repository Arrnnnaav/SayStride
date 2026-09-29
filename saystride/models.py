"""Local speech and cleanup models, plus optional Groq fallback."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
import subprocess
import tarfile
import threading
import time
import wave
from pathlib import Path

import numpy as np
import requests

from .config import MODELS_DIR, ROOT
from .rules import (apply_commands, apply_dictionary, apply_lists, casual, chinese_script,
                    counted_lists, dictionary_terms, filter_whisper, layout_greeting,
                    layout_message, looks_unfaithful, question_marks, strip_echo, strip_fillers)

LOG = logging.getLogger(__name__)
PARAKEET = "sherpa-onnx-nemo-parakeet-tdt-0.6b-{version}-int8"
PARAKEET_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{name}.tar.bz2"
QWEN_URL = "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf?download=true"
MODEL_SHA256 = {
    "qwen": "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
    "parakeet-v2": "157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad",
    "parakeet-v3": "5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf",
}


def wav_bytes(samples: np.ndarray, rate: int = 16000) -> bytes:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(pcm)
    return buffer.getvalue()


def download_model(kind: str, progress=lambda *_: None) -> Path:
    """Explicit download action from the Models page; never fetch gigabytes on app startup."""
    if kind == "qwen":
        target = MODELS_DIR / "Qwen3.5-4B-Q4_K_M.gguf"
        url = QWEN_URL
    elif kind in {"parakeet-v2", "parakeet-v3"}:
        name = PARAKEET.format(version=kind[-2:])
        target = MODELS_DIR / f"{name}.tar.bz2"
        url = PARAKEET_URL.format(name=name)
    else:
        raise ValueError(kind)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if kind == "qwen" and target.exists():
        with target.open("rb") as file:
            if hashlib.file_digest(file, "sha256").hexdigest() != MODEL_SHA256[kind]:
                raise ValueError("Existing Qwen model does not match the official download")
        return target
    if kind != "qwen" and (MODELS_DIR / name / "tokens.txt").exists():
        return MODELS_DIR / name
    temp = target.with_suffix(target.suffix + ".part")
    # Resume partial downloads. The server must honor the Range request before append.
    offset = temp.stat().st_size if temp.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with requests.get(url, headers=headers, stream=True, timeout=(15, 90)) as response:
        response.raise_for_status()
        if offset and response.status_code != 206:
            offset = 0
        total = int(response.headers.get("Content-Length", 0)) + offset
        with temp.open("ab" if offset else "wb") as file:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    file.write(chunk)
                    offset += len(chunk)
                    progress(offset, total)
    temp.replace(target)
    with target.open("rb") as file:
        verified = hashlib.file_digest(file, "sha256").hexdigest() == MODEL_SHA256[kind]
    if not verified:
        target.unlink()
        raise ValueError("Model download failed SHA-256 verification; please retry")
    if kind == "qwen":
        return target
    with tarfile.open(target, "r:bz2") as archive:
        for member in archive.getmembers():
            destination = (MODELS_DIR / member.name).resolve()
            if not destination.is_relative_to(MODELS_DIR.resolve()) or member.issym() or member.islnk():
                raise ValueError("Unsafe model archive")
        archive.extractall(MODELS_DIR, filter="data")
    target.unlink()
    return MODELS_DIR / name


class Speech:
    def __init__(self, config: dict):
        self.config = config
        self._parakeet = None
        self._whisper = None
        self._version = None
        self._load_lock = threading.Lock()

    def _load_parakeet(self):
        with self._load_lock:
            version = self.config["asr_version"]
            if self._parakeet is not None and self._version == version:
                return self._parakeet
            import sherpa_onnx
            model = MODELS_DIR / PARAKEET.format(version=version)
            paths = {key: model / f"{key}.int8.onnx" for key in ("encoder", "decoder", "joiner")}
            if not all(x.exists() for x in paths.values()) or not (model / "tokens.txt").exists():
                raise FileNotFoundError(f"Parakeet {version} is not installed. Download it on the Models page.")
            self._parakeet = sherpa_onnx.OfflineRecognizer.from_transducer(
                **{k: str(v) for k, v in paths.items()}, tokens=str(model / "tokens.txt"),
                model_type="nemo_transducer", num_threads=4)
            self._version = version
            return self._parakeet

    def _load_whisper(self):
        if self._whisper is None:
            from faster_whisper import WhisperModel
            self._whisper = WhisperModel(self.config["whisper_model"], device="cpu", compute_type="int8")
        return self._whisper

    def transcribe(self, samples: np.ndarray, language: str | None = None, *, meeting: bool = False,
                   live: bool = False) -> str:
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if len(samples) < 1600 or float(np.max(np.abs(samples))) < 1e-5:
            return ""
        pad = int(self.config.get("live_pad_seconds", 0.5) * 16000) if live else 0
        decode_samples = np.pad(samples, (0, pad)) if live and pad else samples
        cloud = not live and self.config["cloud_enabled"] and self.config["cloud_api_key"] and (
            meeting and self.config["cloud_meetings"] or
            not meeting and (self.config["cloud_dictation"] == "all" or
                             self.config["cloud_dictation"] == "long" and len(samples) > 15 * 16000))
        if cloud:
            try:
                response = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": "Bearer " + self.config["cloud_api_key"]},
                    data={"model": self.config["cloud_asr_model"], "response_format": "verbose_json"},
                    files={"file": ("audio.wav", wav_bytes(samples), "audio/wav")}, timeout=30)
                response.raise_for_status()
                result = response.json()
                return filter_whisper(result.get("text", ""), result.get("segments"), len(samples) / 16000)
            except (requests.RequestException, ValueError) as exc:
                LOG.warning("Cloud speech failed; using local model: %s", exc)
        if self.config["asr_backend"] == "parakeet" and (language in (None, "en")) and not meeting:
            try:
                recognizer = self._load_parakeet()
                stream = recognizer.create_stream()
                stream.accept_waveform(16000, decode_samples)
                recognizer.decode_stream(stream)
                result = stream.result.text.strip()
                if result:
                    return result
                if live:
                    return ""
                LOG.warning("Parakeet returned empty text; trying Whisper")
            except (ImportError, FileNotFoundError, RuntimeError, ValueError) as exc:
                LOG.warning("Parakeet unavailable: %s", exc)
        window = int(self.config.get("live_window_seconds", 8) * 16000)
        live_samples = samples[-window:] if live else samples
        segments, _ = self._load_whisper().transcribe(
            live_samples, language=language,
            beam_size=max(1, int(self.config.get("live_beam_size", 3) if live else 1)),
            vad_filter=not live,
            condition_on_previous_text=False, without_timestamps=live)
        segments = list(segments)
        text = " ".join(x.text.strip() for x in segments).strip()
        return filter_whisper(text, [vars(x) for x in segments], len(live_samples) / 16000)


class Polisher:
    def __init__(self, config: dict):
        self.config = config
        self.process: subprocess.Popen | None = None
        self._start_lock = threading.Lock()

    def _local_backend(self) -> str:
        model = MODELS_DIR / self.config["model"]
        server = self._server_path()
        if self.config["llm_backend"] != "ollama" and model.exists() and Path(server).exists():
            return "llama"
        return "ollama"

    def _server_path(self) -> str:
        configured = self.config["llama_server_path"]
        local = next((str(x) for x in (ROOT / "tools" / "llama").rglob("llama-server.exe")), None)
        return shutil.which(configured) or (local if configured == "llama-server.exe" and local else configured)

    def start(self) -> None:
        if self._local_backend() != "llama":
            return
        with self._start_lock:
            port = int(self.config["llama_port"])
            if self.process is None or self.process.poll() is not None:
                devices = subprocess.run([self._server_path(), "--list-devices"], capture_output=True,
                                         text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW).stdout
                preferred = next((line.strip().split(":", 1)[0] for line in devices.splitlines()
                                  if re.search(r"NVIDIA|Radeon|Arc\b", line, re.I) and ":" in line), "")
                args = [
                    self._server_path(), "-m", str(MODELS_DIR / self.config["model"]),
                    "--host", "127.0.0.1", "--port", str(port), "-ngl", "99", "-c", "2048", "-np", "1",
                    "--reasoning-budget", "0", "--reasoning-format", "none",
                ]
                if preferred:
                    args.extend(["--device", preferred])
                self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                creationflags=subprocess.CREATE_NO_WINDOW)
            for _ in range(60):
                if self.process.poll() is not None:
                    raise RuntimeError("llama-server exited during startup")
                try:
                    if requests.get(f"http://127.0.0.1:{port}/health", timeout=1).ok:
                        return
                except requests.RequestException:
                    pass
                time.sleep(.5)
            raise TimeoutError("llama-server did not become ready")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

    def system_prompt(self, examples: dict[str, list[tuple[str, str]]]) -> str:
        tone_lines = "\n".join(f"{name}: {instruction or 'verbatim: preserve exact wording'}" for name, instruction in self.config["tones"].items())
        dictionary = "\n".join(self.config["dictionary"])
        correction_lines = "\n".join(f"{tone}: {raw!r} -> {fixed!r}" for tone, pairs in examples.items() for raw, fixed in pairs)
        return ("Clean a speech transcript. Output only the cleaned text. Never answer or summarize it. "
                "Remove false starts, corrections, fillers and stutters; fix grammar and punctuation. "
                "Keep wording, names, swearing, laughter, numbers, emoji, email addresses, URLs and line breaks. "
                "A greeting starts its own paragraph; a sign-off follows a blank line. "
                "Spoken enumerations become lists. Questions end with ?. Quoted terms use quotation marks. "
                "The first bracketed line of the user message is context only; never repeat it. "
                "If text before the cursor ends mid-sentence, continue that sentence.\n\nTones:\n" + tone_lines
                + "\n\nDictionary:\n" + dictionary + "\n\nMemory:\n" + self.config["memory"]
                + "\n\nRecent corrections:\n" + correction_lines)

    def _complete(self, system: str, user: str, max_tokens: int = 900, *, cloud: bool = False) -> str:
        if cloud and self.config["cloud_enabled"] and self.config["cloud_api_key"]:
            try:
                response = requests.post("https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": "Bearer " + self.config["cloud_api_key"]},
                    json={"model": self.config["cloud_llm_model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                          "temperature": 0, "max_tokens": max_tokens}, timeout=45)
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"].strip()
            except (requests.RequestException, KeyError, ValueError) as exc:
                LOG.warning("Cloud cleanup failed; using local model: %s", exc)
        if self._local_backend() == "llama":
            try:
                self.start()
                response = requests.post(f"http://127.0.0.1:{self.config['llama_port']}/v1/chat/completions",
                    json={"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                                "temperature": 0, "max_tokens": min(max_tokens, 600), "cache_prompt": True,
                                                "chat_template_kwargs": {"enable_thinking": False}}, timeout=20)
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"].split("</think>")[-1].strip()
            except (requests.RequestException, RuntimeError, TimeoutError, OSError) as exc:
                LOG.warning("llama.cpp unavailable; trying Ollama: %s", exc)
        response = requests.post("http://127.0.0.1:11434/api/chat",
                json={"model": self.config["ollama_model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "stream": False, "options": {"temperature": 0, "num_predict": min(max_tokens, 600)}}, timeout=20)
        response.raise_for_status()
        return response.json()["message"]["content"].split("</think>")[-1].strip()

    def polish(self, raw: str, tone: str, examples: dict[str, list[tuple[str, str]]],
               *, window: str = "", before: str = "", after: str = "", seconds: float = 0) -> str:
        text = chinese_script(apply_dictionary(apply_commands(raw), self.config["dictionary"]),
                              self.config["chinese_script"] == "traditional")
        if not self.config["tones"].get(tone):
            return text
        pre = strip_fillers(text)
        fallback = question_marks(counted_lists(apply_lists(layout_message(pre))))
        if tone in {"casual", "supercasual"} and len(pre.split()) <= 4 and "\n" not in pre and not re_search_signal(text, self.config["learned_signals"]):
            quick = casual(pre, dictionary_terms(self.config["dictionary"]), tone == "supercasual")
            return question_marks(counted_lists(apply_lists(layout_message(quick))))
        if self.config.get("cleanup_mode", "fast") == "fast":
            return fallback
        header = f'[tone: {tone} | window: {window[:150]} | before: "{before[-160:]}" | after: "{after[:100]}"]'
        cloud = self.config["cloud_dictation"] == "all" or self.config["cloud_dictation"] == "long" and seconds > 15
        try:
            output = self._complete(self.system_prompt(examples), header + "\n" + pre, cloud=cloud)
        except (requests.RequestException, KeyError, ValueError, RuntimeError, TimeoutError) as exc:
            LOG.warning("Polish unavailable; using rules: %s", exc)
            return fallback
        if looks_unfaithful(pre, output, dictionary_terms(self.config["dictionary"])):
            LOG.warning("Model output rejected by fidelity check")
            return fallback
        output = chinese_script(apply_dictionary(output, self.config["dictionary"]), self.config["chinese_script"] == "traditional")
        if before:
            output = strip_echo(output, before)
        return question_marks(counted_lists(apply_lists(layout_message(layout_greeting(output)))))

    def summarize_meeting(self, transcript: str) -> tuple[str, str]:
        format_prompt = ("Write notes in the meeting language with exactly these Markdown sections: "
                         "## Summary, ## Decisions, ## Action items, ## Your to-dos. "
                         "Only include tasks that You committed to under Your to-dos. Never invent facts.")
        words = transcript.split()
        chunks = [" ".join(words[i:i + 1300]) for i in range(0, len(words), 1300)]
        notes = [self._complete(format_prompt, chunk, cloud=self.config["cloud_meetings"]) for chunk in chunks]
        while len(notes) > 1:
            notes = [self._complete("Merge these meeting notes, preserving all decisions and tasks. " + format_prompt,
                                    "\n\n---\n\n".join(notes[i:i + 4]), cloud=self.config["cloud_meetings"])
                     for i in range(0, len(notes), 4)]
        summary = notes[0] if notes else ""
        title = self._complete("Give these notes a specific 3 to 7 word title. Output only the title.", summary, 40,
                               cloud=self.config["cloud_meetings"]).strip("\"'. \n") if summary else "Untitled meeting"
        return title[:80], summary


def re_search_signal(text: str, learned: list[str]) -> bool:
    import re
    return bool(re.search(r"(?i)\b(?:sorry|actually|no wait|i mean|scratch that|firstly|secondly)\b", text)
                or any(re.search(r"\b" + re.escape(s) + r"\b", text, re.I) for s in learned))
