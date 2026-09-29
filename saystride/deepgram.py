"""Deepgram Nova-3 comparison and optional live dictation transport."""
from __future__ import annotations

import json
import queue
import threading
from urllib.parse import urlencode

import numpy as np
import requests
import websocket

from .models import wav_bytes
from .rules import dictionary_terms


def _params(config: dict, *, live: bool) -> dict:
    params = {"model": config["deepgram_model"], "language": config["deepgram_language"],
              "punctuate": "true"}
    if live:
        params.update(encoding="linear16", sample_rate=16000, channels=1,
                      interim_results="true", endpointing=300)
    if config.get("deepgram_keyterms"):
        terms = list(dict.fromkeys(dictionary_terms(config.get("dictionary", []))))[:20]
        if terms:
            params["keyterm"] = terms
    return params


def transcribe_file(samples: np.ndarray, key: str, config: dict) -> str:
    response = requests.post("https://api.deepgram.com/v1/listen", params=_params(config, live=False),
                             headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav"},
                             data=wav_bytes(samples), timeout=(5, 30))
    response.raise_for_status()
    return response.json()["results"]["channels"][0]["alternatives"][0]["transcript"].strip()


class DeepgramStream:
    """Send mic frames off the audio callback; publish interim and committed words."""

    def __init__(self, key: str, config: dict, on_text):
        url = "wss://api.deepgram.com/v1/listen?" + urlencode(_params(config, live=True), doseq=True)
        self.ws = websocket.create_connection(url, header=[f"Authorization: Token {key}"], timeout=5)
        self.ws.settimeout(.25)
        self.on_text = on_text
        self.audio = queue.Queue(maxsize=300)
        self.committed: list[str] = []
        self.interim = ""
        self.error = None
        self.finalized = threading.Event()
        self.sender = threading.Thread(target=self._send, daemon=True, name="saystride-deepgram-send")
        self.receiver = threading.Thread(target=self._receive, daemon=True, name="saystride-deepgram-recv")
        self.sender.start()
        self.receiver.start()

    def feed(self, samples: np.ndarray) -> None:
        if self.error:
            return
        try:
            self.audio.put_nowait((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        except queue.Full:
            self.error = RuntimeError("Deepgram audio fell behind the microphone")

    def _send(self):
        try:
            while True:
                chunk = self.audio.get()
                if chunk is None:
                    break
                self.ws.send(chunk, opcode=websocket.ABNF.OPCODE_BINARY)
            self.ws.send(json.dumps({"type": "Finalize"}))
        except Exception:
            self.error = RuntimeError("Deepgram streaming connection failed")
            self.finalized.set()

    def _receive(self):
        try:
            while not self.finalized.is_set():
                try:
                    message = self.ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not message:
                    break
                item = json.loads(message)
                if item.get("type") != "Results":
                    continue
                text = item.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "").strip()
                if item.get("is_final"):
                    if text:
                        self.committed.append(text)
                    self.interim = ""
                else:
                    self.interim = text
                self.on_text(" ".join((*self.committed, self.interim)).strip())
                if item.get("from_finalize"):
                    self.finalized.set()
        except Exception:
            self.error = RuntimeError("Deepgram stopped returning live text")
        finally:
            self.finalized.set()

    def finish(self) -> str:
        self.audio.put(None, timeout=2)
        self.sender.join(timeout=5)
        complete = self.finalized.wait(3)
        text = " ".join(self.committed).strip()
        try:
            self.ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass
        self.ws.close()
        self.receiver.join(timeout=1)
        if self.error:
            raise self.error
        if not complete:
            raise TimeoutError("Deepgram final result timed out")
        return text

    def close(self) -> None:
        self.finalized.set()
        try:
            self.audio.put_nowait(None)
        except queue.Full:
            pass
        self.ws.close()
