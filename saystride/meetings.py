"""Opt-in two-channel meeting capture and note generation on Windows."""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime
from uuid import uuid4

import numpy as np
import psutil
import pyaudiowpatch as pyaudio
import sounddevice as sd

from .rules import meeting_line

LOG = logging.getLogger(__name__)
CALL_APPS = {"zoom.exe": "Zoom", "teams.exe": "Teams", "ms-teams.exe": "Teams",
             "webex.exe": "Webex", "discord.exe": "Discord", "slack.exe": "Slack",
             "whatsapp.exe": "WhatsApp", "telegram.exe": "Telegram"}


def detected_call() -> str | None:
    """Offer notes when a known call app is open; capture still requires an explicit click."""
    for proc in psutil.process_iter(["name"]):
        try:
            label = CALL_APPS.get((proc.info["name"] or "").lower())
            if label:
                return label
        except psutil.Error:
            continue
    return None


def resample(samples: np.ndarray, source_rate: int) -> np.ndarray:
    samples = np.asarray(samples, dtype=np.float32)
    if source_rate == 16000:
        return samples
    if not len(samples):
        return samples
    count = round(len(samples) * 16000 / source_rate)
    return np.interp(np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples).astype(np.float32)


class Segmenter:
    """Energy gate with 300 ms pre-roll and 700 ms end silence."""
    def __init__(self, emit, floor: float = .006):
        self.emit = emit
        self.floor = floor
        self.noise = floor / 4
        self.pre = np.empty(0, dtype=np.float32)
        self.active = []
        self.quiet = 0
        self.started = 0.0

    def push(self, samples: np.ndarray, timestamp: float):
        samples = np.asarray(samples, dtype=np.float32)
        for i in range(0, len(samples), 320):
            frame = samples[i:i + 320]
            if not len(frame):
                continue
            rms = float(np.sqrt(np.mean(frame * frame)))
            speech = rms > max(self.floor, self.noise * 4)
            if not self.active and not speech:
                self.noise = .98 * self.noise + .02 * rms
                self.pre = np.concatenate((self.pre, frame))[-4800:]
                continue
            if not self.active:
                self.active = [self.pre.copy()]
                self.started = timestamp + i / 16000 - len(self.pre) / 16000
                self.pre = np.empty(0, dtype=np.float32)
            self.active.append(frame.copy())
            self.quiet = 0 if speech else self.quiet + len(frame)
            if self.quiet >= 11200:
                utterance = np.concatenate(self.active)
                if len(utterance) >= 8000:
                    self.emit(utterance, self.started)
                self.active = []
                self.quiet = 0

    def flush(self):
        if self.active:
            utterance = np.concatenate(self.active)
            if len(utterance) >= 8000:
                self.emit(utterance, self.started)
            self.active = []


class MeetingSession:
    def __init__(self, store, speech, polisher, on_update=lambda *_: None):
        self.store, self.speech, self.polisher = store, speech, polisher
        self.on_update = on_update
        self.meeting = None
        self._running = False
        self._threads = []
        self._mic = None
        self._speaker = None
        self._pa = None
        self._queue = []
        self._lock = threading.Lock()
        self._segments = {}

    def start(self, app: str | None = None):
        if self._running:
            return
        self.meeting = dict(id=str(uuid4()), app=app, title=f"Meeting · {datetime.now():%d %b %H:%M}",
                            start=datetime.now().astimezone().isoformat(), end=None, segments=[],
                            notes="", todos=[])
        self.store.save_meeting(self.meeting)
        self._running = True
        start = time.monotonic()
        self._segments = {
            "You": Segmenter(lambda audio, t: self._enqueue("You", audio, t)),
            "Them": Segmenter(lambda audio, t: self._enqueue("Them", audio, t), floor=.010),
        }
        def mic_callback(data, frames, timing, status):
            if self._running:
                self._segments["You"].push(data[:, 0].copy(), time.monotonic() - start)
        self._mic = sd.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=320,
                                   callback=mic_callback)
        self._mic.start()
        self._pa = pyaudio.PyAudio()
        device = self._pa.get_default_wasapi_loopback()
        rate = int(device["defaultSampleRate"])
        channels = min(2, int(device["maxInputChannels"]))
        self._speaker = self._pa.open(format=pyaudio.paFloat32, channels=channels, rate=rate,
                                      input=True, input_device_index=device["index"], frames_per_buffer=rate // 10)
        def read_speaker():
            while self._running:
                try:
                    raw = self._speaker.read(rate // 10, exception_on_overflow=False)
                    data = np.frombuffer(raw, dtype=np.float32).reshape(-1, channels).mean(axis=1)
                    self._segments["Them"].push(resample(data, rate), time.monotonic() - start)
                except Exception as exc:
                    LOG.warning("Speaker capture ended: %s", exc)
                    break
        self._threads = [threading.Thread(target=read_speaker, daemon=True, name="saystride-speaker"),
                         threading.Thread(target=self._transcribe_loop, daemon=True, name="saystride-meeting-asr")]
        for thread in self._threads:
            thread.start()
        self.on_update(self.meeting)

    def _enqueue(self, speaker, samples, timecode):
        with self._lock:
            self._queue.append((speaker, samples, timecode))

    def _transcribe_loop(self):
        last_notes = time.monotonic()
        while self._running or self._queue:
            with self._lock:
                item = self._queue.pop(0) if self._queue else None
            if item:
                speaker, audio, timecode = item
                try:
                    raw = self.speech.transcribe(audio, meeting=True)
                    text = meeting_line(raw, self.store.config["dictionary"])
                    if text:
                        self.meeting["segments"].append({"speaker": speaker, "time": round(timecode, 1), "text": text})
                        self.store.save_meeting(self.meeting)
                        self.on_update(self.meeting)
                except Exception:
                    LOG.exception("Meeting transcription failed")
            elif self._running:
                time.sleep(.2)
            if self._running and time.monotonic() - last_notes > 180 and self.meeting["segments"]:
                self.refresh_notes()
                last_notes = time.monotonic()

    def refresh_notes(self):
        if not self.meeting or not self.meeting["segments"]:
            return
        transcript = "\n".join(f"[{int(x['time']//60)}:{int(x['time']%60):02d}] {x['speaker']}: {x['text']}"
                               for x in self.meeting["segments"])
        try:
            title, notes = self.polisher.summarize_meeting(transcript)
            self.meeting["title"], self.meeting["notes"] = title, notes
            previous = {x["text"]: x["done"] for x in self.meeting.get("todos", [])}
            section = notes.split("## Your to-dos", 1)[-1].split("## ", 1)[0] if "## Your to-dos" in notes else ""
            todos = [re.sub(r"^[-*]\s*", "", line).strip() for line in section.splitlines() if line.strip().startswith(("-", "*"))]
            self.meeting["todos"] = [{"text": task, "done": previous.get(task, False)} for task in todos if task.lower() != "none"]
            self.store.save_meeting(self.meeting)
            self.on_update(self.meeting)
        except Exception:
            LOG.exception("Meeting notes failed")

    def stop(self):
        if not self._running:
            return
        self._running = False
        for segmenter in self._segments.values():
            segmenter.flush()
        if self._mic:
            self._mic.stop()
            self._mic.close()
            self._mic = None
        if self._speaker:
            self._speaker.stop_stream()
            self._speaker.close()
            self._speaker = None
        if self._pa:
            self._pa.terminate()
            self._pa = None
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=10)
        self.meeting["end"] = datetime.now().astimezone().isoformat()
        self.store.save_meeting(self.meeting)
        self.on_update(self.meeting)
        threading.Thread(target=self.refresh_notes, daemon=True).start()
