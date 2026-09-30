"""Standalone Windows tray app and settings window."""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
import pystray
import sounddevice as sd
import win32con
import win32gui
from PIL import Image, ImageDraw

from .config import APP_DIR, CLIPS_DIR, MODELS_DIR, deepgram_key, load, save
from .deepgram import DeepgramStream
from .meetings import MeetingSession, detected_call
from .models import Polisher, Speech, download_model, wav_bytes
from .store import Store
from .windows import (Hotkey, Recorder, field_context, foreground, modifiers_down, paste,
                      replace_append_live, replace_live, revise_append_live, wait_for_modifiers)

LOG = logging.getLogger(__name__)


def _icon():
    image = Image.new("RGBA", (64, 64), "#192733")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((22, 10, 42, 42), radius=10, fill="#52d6bd")
    draw.arc((14, 12, 50, 52), 0, 180, fill="white", width=5)
    draw.line((32, 52, 32, 60), fill="white", width=5)
    return image


class App:
    def __init__(self):
        self.config = load()
        self.store = Store(self.config)
        self.speech = Speech(self.config)
        self.polisher = Polisher(self.config)
        self.events = queue.Queue()
        self.recorder = Recorder(device=self.config["microphone"], on_level=lambda x: self.events.put(("level", x)),
                                 on_audio=self._send_cloud_audio)
        self.deepgram = None
        self.cloud_lock = threading.Lock()
        self.cloud_buffer = None
        self.meeting = MeetingSession(self.store, self.speech, self.polisher,
                                      on_update=lambda x: self.events.put(("meeting", x)))
        self.root = tk.Tk()
        self.root.title("SayStride for Windows")
        self.root.geometry("950x650")
        self.root.minsize(720, 500)
        self.root.protocol("WM_DELETE_WINDOW", self.root.withdraw)
        self.state = "idle"
        self.toggled = False
        self.pressed_at = 0.0
        self.target = 0
        self.target_title = ""
        self.target_app = ""
        self.before = self.after = ""
        self.tone = "supercasual"
        self.frozen_at = 0
        self.frozen_text = ""
        self.live_text = ""
        self.live_enabled = False
        self.live_append_mode = False
        self.levels = deque([0.0] * 25, maxlen=25)
        self.live_stop = threading.Event()
        self.live_thread = None
        self.started_at = 0.0
        self.first_text_at = None
        self.first_written_at = None
        self.live_updates = 0
        self.live_rewrites = 0
        self.stopped_at = 0.0
        self.download_active = None
        self.record_mode = "hold"
        self.hotkey = Hotkey(lambda mode: self.events.put(("down", mode)),
                             lambda: self.events.put(("up", None)),
                             lambda: self.events.put(("cancel", None)),
                             lambda message: self.events.put(("hotkey_error", message)))
        self._build()
        self.hotkey.start()
        self.tray = pystray.Icon("SayStride", _icon(), "SayStride for Windows", menu=pystray.Menu(
            pystray.MenuItem("Open SayStride", lambda *_: self.events.put(("open", None)), default=True),
            pystray.MenuItem("Start/stop meeting notes", lambda *_: self.events.put(("toggle_meeting", None))),
            pystray.MenuItem("Quit", lambda *_: self.events.put(("quit", None)))))
        threading.Thread(target=self.tray.run, daemon=True, name="saystride-tray").start()
        threading.Thread(target=self._warm_model, daemon=True, name="saystride-model").start()
        threading.Thread(target=self._warm_speech, daemon=True, name="saystride-speech").start()
        self.root.after(50, self._poll)
        self.root.after(10000, self._offer_call)
        if not self.config["window_at_launch"]:
            self.root.withdraw()

    def run(self):
        self.root.mainloop()

    def _warm_model(self):
        if self.config.get("cleanup_mode", "fast") == "fast":
            return
        try:
            self.polisher.start()
        except Exception as exc:
            LOG.warning("Local cleanup model could not start: %s", exc)

    def _warm_speech(self):
        try:
            if self.config["asr_backend"] == "parakeet":
                self.speech._load_parakeet()
            else:
                self.speech._load_whisper()
        except Exception as exc:
            LOG.warning("Local speech model could not warm: %s", exc)

    def _build(self):
        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="SayStride", font=("Segoe UI", 20, "bold")).pack(side="left")
        self.status = tk.StringVar(value="Ready · Hold F8 to dictate · F9 starts/stops with Esc")
        ttk.Label(top, textvariable=self.status).pack(side="right")
        tabs = ttk.Notebook(self.root)
        tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tabs = tabs
        self._home(tabs)
        self._dictionary(tabs)
        self._tones(tabs)
        self._memory(tabs)
        self._models(tabs)
        self._meetings(tabs)
        self._settings(tabs)
        self.pill = tk.Toplevel(self.root)
        self.pill.overrideredirect(True)
        self.pill.attributes("-topmost", True)
        self.pill.attributes("-alpha", .94)
        self.pill.configure(bg="#192733")
        self.pill_label = tk.Label(self.pill, text="Listening…", fg="white", bg="#192733",
                                   font=("Segoe UI", 11), padx=20, pady=12, wraplength=450)
        self.pill_label.pack()
        self.wave = tk.Canvas(self.pill, width=225, height=24, bg="#192733", highlightthickness=0)
        self.wave.pack(pady=(0, 8))
        self.pill.withdraw()
        self.pill.update_idletasks()
        hwnd = win32gui.GetAncestor(self.pill.winfo_id(), win32con.GA_ROOT)
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                               style | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW)

    def _pill(self, text):
        if not self.config["overlay"]:
            return
        self.pill_label.configure(text=text[:180])
        self.pill.update_idletasks()
        x = (self.pill.winfo_screenwidth() - self.pill.winfo_reqwidth()) // 2
        y = self.pill.winfo_screenheight() - self.pill.winfo_reqheight() - 80
        self.pill.geometry(f"+{x}+{y}")
        self.pill.deiconify()

    def _waveform(self, level):
        self.levels.append(min(1.0, level * 12))
        self.wave.delete("all")
        for i, value in enumerate(self.levels):
            height = 3 + int(value * 19)
            self.wave.create_rectangle(4 + i * 9, 12 - height / 2, 9 + i * 9, 12 + height / 2,
                                       fill="#6ce5d0", outline="")

    def _home(self, tabs):
        page = ttk.Frame(tabs, padding=10)
        tabs.add(page, text="Home / History")
        self.stats = tk.StringVar()
        ttk.Label(page, textvariable=self.stats).pack(anchor="w", pady=5)
        self.history = ttk.Treeview(page, columns=("date", "tone", "app", "text"), show="headings")
        for name, width in (("date", 145), ("tone", 100), ("app", 115), ("text", 520)):
            self.history.heading(name, text=name.title())
            self.history.column(name, width=width, stretch=name == "text")
        self.history.pack(fill="both", expand=True)
        buttons = ttk.Frame(page)
        buttons.pack(fill="x", pady=6)
        ttk.Button(buttons, text="Copy text", command=self._copy_history).pack(side="left", padx=3)
        ttk.Button(buttons, text="Correct selected", command=self._correct_history).pack(side="left", padx=3)
        ttk.Button(buttons, text="Refresh", command=self._refresh_history).pack(side="left", padx=3)
        self._refresh_history()

    def _selected_history(self):
        ids = self.history.selection()
        return next((x for x in self.store.history if ids and x["id"] == ids[0]), None)

    def _refresh_history(self):
        self.history.delete(*self.history.get_children())
        for item in self.store.history:
            self.history.insert("", "end", iid=item["id"], values=(item["date"][:16], item["tone"],
                                item.get("app", ""), item["text"].replace("\n", " ⏎ ")[:200]))
        count = len(self.store.history)
        words = sum(len(x["text"].split()) for x in self.store.history)
        labeled = sum(bool(x.get("reference_raw")) for x in self.store.history)
        self.stats.set(f"{count} recent dictations · {words} words · {labeled} speech references · "
                       f"{sum(bool(x.get('correction')) for x in self.store.history)} corrections")

    def _copy_history(self):
        item = self._selected_history()
        if item:
            self.root.clipboard_clear()
            self.root.clipboard_append(item["text"])

    def _correct_history(self):
        item = self._selected_history()
        if not item:
            return
        popup = tk.Toplevel(self.root)
        popup.title("Correct dictation")
        popup.geometry("650x340")
        ttk.Label(popup, text="Raw transcript:").pack(anchor="w", padx=10)
        ttk.Label(popup, text=item["raw"], wraplength=610).pack(anchor="w", padx=10, pady=5)
        ttk.Label(popup, text="What you actually said (for ASR evaluation):").pack(anchor="w", padx=10)
        spoken = ttk.Entry(popup, width=85)
        spoken.pack(fill="x", padx=10)
        if item.get("reference_raw"):
            spoken.insert(0, item["reference_raw"])
        ttk.Label(popup, text="Desired final text:").pack(anchor="w", padx=10, pady=(8, 0))
        box = tk.Text(popup, height=8, wrap="word")
        box.pack(fill="both", expand=True, padx=10)
        box.insert("1.0", item.get("correction") or item["text"])
        def submit():
            self.store.correct(item["id"], box.get("1.0", "end").strip(), spoken.get().strip())
            popup.destroy()
            self._refresh_history()
            self._fill_dictionary()
        ttk.Button(popup, text="Save correction", command=submit).pack(pady=8)

    def _dictionary(self, tabs):
        page = ttk.Frame(tabs, padding=10)
        tabs.add(page, text="Dictionary")
        ttk.Label(page, text="One per line: Term or Term: sound-alike, sound-alike").pack(anchor="w")
        self.dictionary = tk.Text(page, wrap="word")
        self.dictionary.pack(fill="both", expand=True, pady=8)
        ttk.Button(page, text="Save dictionary", command=self._save_dictionary).pack(anchor="e")
        self._fill_dictionary()

    def _fill_dictionary(self):
        self.dictionary.delete("1.0", "end")
        self.dictionary.insert("1.0", "\n".join(self.config["dictionary"]))

    def _save_dictionary(self):
        self.config["dictionary"] = [line.strip() for line in self.dictionary.get("1.0", "end").splitlines() if line.strip()]
        save(self.config)
        self.status.set("Dictionary saved")

    def _tones(self, tabs):
        page = ttk.Frame(tabs, padding=10)
        tabs.add(page, text="Tones / Apps")
        bar = ttk.Frame(page)
        bar.pack(fill="x")
        self.tone_name = tk.StringVar(value="supercasual")
        self.tone_picker = ttk.Combobox(bar, textvariable=self.tone_name, values=list(self.config["tones"]), width=20)
        self.tone_picker.pack(side="left")
        self.tone_picker.bind("<<ComboboxSelected>>", lambda _: self._show_tone())
        ttk.Button(bar, text="Save tone", command=self._save_tone).pack(side="left", padx=6)
        ttk.Label(page, text="Empty instruction means verbatim; the language model is skipped.").pack(anchor="w", pady=5)
        self.tone_editor = tk.Text(page, height=11, wrap="word")
        self.tone_editor.pack(fill="both", expand=True)
        self._show_tone()
        ttk.Label(page, text="App tones: executable name = tone,alternate tone (one per line)").pack(anchor="w", pady=(10, 2))
        self.app_tones = tk.Text(page, height=6, wrap="none")
        self.app_tones.pack(fill="x")
        self.app_tones.insert("1.0", "\n".join(f"{app} = {t['tone']},{t['alt']}" for app, t in self.config["app_tones"].items()))
        ttk.Button(page, text="Save app tones", command=self._save_app_tones).pack(anchor="e", pady=5)

    def _show_tone(self):
        self.tone_editor.delete("1.0", "end")
        self.tone_editor.insert("1.0", self.config["tones"].get(self.tone_name.get(), ""))

    def _save_tone(self):
        name = self.tone_name.get().strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,30}", name):
            messagebox.showerror("Tone", "Use a short name with letters, numbers, _ or -.")
            return
        self.config["tones"][name] = self.tone_editor.get("1.0", "end").strip()
        save(self.config)
        self.tone_picker.configure(values=list(self.config["tones"]))
        self.status.set(f"Tone {name} saved")

    def _save_app_tones(self):
        result = {}
        for line in self.app_tones.get("1.0", "end").splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r"\s*([^=]+?)\s*=\s*([\w-]+)\s*,\s*([\w-]+)\s*", line)
            if not match or match.group(2) not in self.config["tones"] or match.group(3) not in self.config["tones"]:
                messagebox.showerror("App tones", f"Invalid line: {line}")
                return
            result[match.group(1).strip()] = {"tone": match.group(2), "alt": match.group(3)}
        self.config["app_tones"] = result
        save(self.config)
        self.status.set("App tones saved")

    def _memory(self, tabs):
        page = ttk.Frame(tabs, padding=10)
        tabs.add(page, text="Memory")
        ttk.Label(page, text="Names, projects and spelling conventions the cleanup model should remember.").pack(anchor="w")
        self.memory = tk.Text(page, wrap="word")
        self.memory.pack(fill="both", expand=True, pady=8)
        self.memory.insert("1.0", self.config["memory"])
        ttk.Button(page, text="Save memory", command=self._save_memory).pack(anchor="e")

    def _save_memory(self):
        self.config["memory"] = self.memory.get("1.0", "end").strip()
        save(self.config)
        self.status.set("Memory saved")

    def _models(self, tabs):
        page = ttk.Frame(tabs, padding=10)
        tabs.add(page, text="Models")
        self.models_status = tk.StringVar()
        self.models_hint = tk.StringVar()
        ttk.Label(page, textvariable=self.models_status, wraplength=800).pack(anchor="w", pady=(8, 2))
        ttk.Label(page, textvariable=self.models_hint, wraplength=800).pack(anchor="w", pady=(0, 8))
        self._refresh_models()
        selection = ttk.Frame(page)
        selection.pack(anchor="w", pady=4)
        ttk.Label(selection, text="Parakeet version").pack(side="left")
        version = tk.StringVar(value=self.config["asr_version"])
        ttk.Combobox(selection, textvariable=version, values=["v2", "v3"], state="readonly", width=6).pack(side="left", padx=6)
        version.trace_add("write", lambda *_: self._set("asr_version", version.get()))
        ttk.Label(selection, text="Cleanup backend").pack(side="left", padx=(16, 0))
        backend = tk.StringVar(value=self.config["llm_backend"])
        ttk.Combobox(selection, textvariable=backend, values=["auto", "llama", "ollama"], state="readonly", width=10).pack(side="left", padx=6)
        backend.trace_add("write", lambda *_: self._set("llm_backend", backend.get()))
        self.download_buttons = {}
        for kind, label in (("parakeet-v2", "Download Parakeet v2 · English"),
                            ("parakeet-v3", "Download Parakeet v3 · multilingual"),
                            ("qwen", "Download Qwen3.5 4B GGUF · 2.7 GB")):
            button = ttk.Button(page, text=label, command=lambda k=kind: self._download(k))
            button.pack(anchor="w", pady=4)
            self.download_buttons[kind] = button
        ttk.Button(page, text="Use existing GGUF file", command=self._choose_model).pack(anchor="w", pady=8)
        ttk.Button(page, text="Choose llama-server.exe", command=self._choose_llama).pack(anchor="w", pady=4)
        ttk.Button(page, text="Open models folder", command=lambda: __import__("os").startfile(MODELS_DIR)).pack(anchor="w", pady=4)
        ttk.Label(page, text="Speech falls back to local faster-whisper if Parakeet is missing. "
                        "Cleanup uses llama-server with GGUF, or local Ollama if configured.", wraplength=800).pack(anchor="w", pady=12)

    def _refresh_models(self):
        models = ", ".join(x.name for x in MODELS_DIR.glob("*.gguf")) or "none"
        parakeet = ", ".join(x.name for x in MODELS_DIR.glob("sherpa-onnx-*") if x.is_dir()) or "none"
        speech_ready = any((x / "tokens.txt").exists() and (x / "encoder.int8.onnx").exists()
                           for x in MODELS_DIR.glob("sherpa-onnx-*") if x.is_dir())
        cleanup_ready = (self.config.get("cleanup_mode", "fast") == "fast" or
                         bool(models != "none" and Path(self.polisher._server_path()).exists()))
        ready = speech_ready and cleanup_ready
        self.models_status.set(f"{'READY TO USE' if ready else 'SETUP NEEDED'}\n"
                               f"GGUF: {models}\nSpeech: {parakeet}\nData: {APP_DIR}\n"
                               f"LLM: {self.config['llm_backend']} / {self.config['ollama_model']}")
        self.models_hint.set("Hold F8 to dictate live, or press F9 once and press Esc to stop. "
                             + ("Everything needed is installed." if ready else
                                "Download the missing model above, then try a short dictation."))
        if hasattr(self, "download_buttons"):
            for kind, button in self.download_buttons.items():
                button.configure(state="disabled" if self.download_active else "normal")

    def _download(self, kind):
        if self.download_active:
            return
        self.download_active = kind
        self.models_status.set(f"Downloading {kind}…")
        self.models_hint.set("You can keep using the window. This download can take a while and resumes if interrupted.")
        self._refresh_models()
        def worker():
            try:
                path = download_model(kind, lambda done, total: self.events.put(("download_progress", (kind, done, total))))
                self.events.put(("download_done", str(path)))
            except Exception as exc:
                self.events.put(("download_error", f"Download failed: {exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def _choose_model(self):
        path = filedialog.askopenfilename(filetypes=[("GGUF models", "*.gguf")])
        if path:
            from pathlib import Path
            file = Path(path)
            if file.parent != MODELS_DIR:
                messagebox.showinfo("Models", f"Copy the GGUF to {MODELS_DIR} first, then choose it there.")
                return
            self.config["model"] = file.name
            save(self.config)
            self.polisher.stop()
            self._refresh_models()

    def _choose_llama(self):
        path = filedialog.askopenfilename(filetypes=[("Windows programs", "*.exe")])
        if path:
            self.config["llama_server_path"] = path
            save(self.config)
            self._refresh_models()

    def _meetings(self, tabs):
        page = ttk.Frame(tabs, padding=10)
        tabs.add(page, text="Meetings")
        buttons = ttk.Frame(page)
        buttons.pack(fill="x")
        self.meeting_button = ttk.Button(buttons, text="Start meeting notes", command=self._toggle_meeting)
        self.meeting_button.pack(side="left")
        ttk.Button(buttons, text="Refresh notes", command=lambda: threading.Thread(target=self.meeting.refresh_notes, daemon=True).start()).pack(side="left", padx=6)
        ttk.Button(buttons, text="Export Markdown", command=self._export_meeting).pack(side="left", padx=6)
        self.meeting_list = ttk.Combobox(page, state="readonly")
        self.meeting_list.pack(fill="x", pady=8)
        self.meeting_list.bind("<<ComboboxSelected>>", lambda _: self._show_meeting())
        self.meeting_text = tk.Text(page, wrap="word")
        self.meeting_text.pack(fill="both", expand=True)
        ttk.Label(page, text="Your to-dos").pack(anchor="w")
        self.todo_list = tk.Listbox(page, height=4)
        self.todo_list.pack(fill="x")
        self.todo_list.bind("<Double-Button-1>", lambda _: self._toggle_todo())
        ttk.Button(page, text="Mark selected done / undone", command=self._toggle_todo).pack(anchor="w", pady=4)
        self._refresh_meetings()

    def _refresh_meetings(self):
        self._meetings_data = self.store.meetings()
        self.meeting_list.configure(values=[f"{x['start'][:16]} · {x['title']}" for x in self._meetings_data])
        if self._meetings_data:
            self.meeting_list.current(0)
            self._show_meeting()

    def _show_meeting(self):
        i = self.meeting_list.current()
        if i < 0 or i >= len(self._meetings_data):
            return
        meeting = self._meetings_data[i]
        transcript = "\n".join(f"[{int(x['time']//60)}:{int(x['time']%60):02d}] {x['speaker']}: {x['text']}" for x in meeting["segments"])
        self.meeting_text.delete("1.0", "end")
        self.meeting_text.insert("1.0", f"# {meeting['title']}\n\n{meeting['notes']}\n\n## Transcript\n{transcript}")
        self.todo_list.delete(0, "end")
        for todo in meeting.get("todos", []):
            self.todo_list.insert("end", ("☑ " if todo["done"] else "☐ ") + todo["text"])

    def _toggle_todo(self):
        choice = self.todo_list.curselection()
        if not choice or self.meeting_list.current() < 0:
            return
        meeting = self._meetings_data[self.meeting_list.current()]
        meeting["todos"][choice[0]]["done"] = not meeting["todos"][choice[0]]["done"]
        self.store.save_meeting(meeting)
        self._show_meeting()

    def _export_meeting(self):
        i = self.meeting_list.current()
        if i < 0:
            return
        path = filedialog.asksaveasfilename(defaultextension=".md", filetypes=[("Markdown", "*.md")])
        if path:
            from pathlib import Path
            Path(path).write_text(self.meeting_text.get("1.0", "end").strip(), encoding="utf-8")

    def _toggle_meeting(self):
        if self.meeting._running:
            self.meeting_button.configure(text="Start meeting notes")
            threading.Thread(target=self.meeting.stop, daemon=True).start()
        else:
            self.meeting_button.configure(text="Stop meeting notes")
            threading.Thread(target=self._start_meeting, daemon=True).start()

    def _start_meeting(self):
        try:
            self.meeting.start(detected_call())
        except Exception as exc:
            self.events.put(("error", f"Meeting capture failed: {exc}"))
            self.events.put(("meeting_stopped", None))

    def _settings(self, tabs):
        page = ttk.Frame(tabs, padding=10)
        tabs.add(page, text="Settings")
        diagnostics_row = ttk.Frame(page)
        diagnostics_row.pack(fill="x", pady=(0, 6))
        ttk.Label(diagnostics_row, text="Troubleshooting").pack(side="left")
        ttk.Button(diagnostics_row, text="View dictation diagnostics", command=self._show_diagnostics).pack(side="right")
        for key, label in (("keep_clips", "Keep dictation clips for evaluation"),
                           ("overlay", "Show floating listening indicator"),
                           ("field_context", "Read text around the cursor when available"),
                           ("live_text", "Type live text in readable fields (experimental)"),
                           ("meeting_detect", "Offer meeting notes when a call app is open"),
                           ("window_at_launch", "Open window at launch"),
                           ("cloud_enabled", "Use Groq cloud when selected below"),
                           ("deepgram_keyterms", "Boost Dictionary words in Deepgram (uses more trial credit)"),
                           ("cloud_meetings", "Use cloud for meeting speech and notes")):
            var = tk.BooleanVar(value=self.config[key])
            ttk.Checkbutton(page, text=label, variable=var,
                            command=lambda k=key, v=var: self._set(k, v.get())).pack(anchor="w", pady=3)
        row = ttk.Frame(page)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="Speech backend").pack(side="left")
        asr = tk.StringVar(value=self.config["asr_backend"])
        ttk.Combobox(row, textvariable=asr, values=["parakeet", "whisper"], state="readonly", width=16).pack(side="left", padx=6)
        asr.trace_add("write", lambda *_: self._set("asr_backend", asr.get()))
        ttk.Label(row, text="Cleanup").pack(side="left", padx=(20, 0))
        cleanup = tk.StringVar(value=self.config.get("cleanup_mode", "fast"))
        ttk.Combobox(row, textvariable=cleanup, values=["fast", "quality"], state="readonly", width=10).pack(side="left", padx=6)
        cleanup.trace_add("write", lambda *_: self._set("cleanup_mode", cleanup.get()))
        provider = tk.StringVar(value=self.config["dictation_provider"])
        self.provider_status = tk.StringVar()
        ttk.Label(page, textvariable=self.provider_status, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(4, 2))
        ttk.Label(page, text="Live dictation provider (Deepgram sends microphone audio to the cloud):").pack(anchor="w")
        ttk.Combobox(page, textvariable=provider, values=["local", "deepgram"], state="readonly", width=18).pack(anchor="w")
        provider.trace_add("write", lambda *_: self._set_provider(provider.get()))
        ttk.Label(page, text="Deepgram key is read from the root .env file (DEEPGRAM=...) or DEEPGRAM_API_KEY.").pack(anchor="w")
        self._refresh_provider_status()
        ttk.Label(row, text="Cloud dictation").pack(side="left", padx=(20, 0))
        cloud = tk.StringVar(value=self.config["cloud_dictation"])
        ttk.Combobox(row, textvariable=cloud, values=["off", "long", "all"], state="readonly", width=10).pack(side="left", padx=6)
        cloud.trace_add("write", lambda *_: self._set("cloud_dictation", cloud.get()))
        ttk.Label(page, text="Groq API key (stored in local config.json):").pack(anchor="w")
        self.key_entry = ttk.Entry(page, show="*", width=70)
        self.key_entry.insert(0, self.config["cloud_api_key"])
        self.key_entry.pack(anchor="w", pady=3)
        ttk.Button(page, text="Save key", command=self._save_key).pack(anchor="w")
        row2 = ttk.Frame(page)
        row2.pack(fill="x", pady=14)
        ttk.Label(row2, text="Microphone").pack(side="left")
        devices = [f"{i}: {x['name']}" for i, x in enumerate(sd.query_devices()) if x["max_input_channels"] > 0]
        mic = tk.StringVar(value="Default" if self.config["microphone"] is None else next((x for x in devices if x.startswith(str(self.config["microphone"]) + ":")), "Default"))
        picker = ttk.Combobox(row2, textvariable=mic, values=["Default"] + devices, width=55, state="readonly")
        picker.pack(side="left", padx=6)
        picker.bind("<<ComboboxSelected>>", lambda _: self._set_mic(mic.get()))

    def _show_diagnostics(self):
        rows = self.store.diagnostics()
        window = tk.Toplevel(self.root)
        window.title("SayStride diagnostics")
        window.geometry("940x430")
        window.minsize(720, 300)
        page = ttk.Frame(window, padding=10)
        page.pack(fill="both", expand=True)
        failures = sum(bool(row.get("error")) or row.get("insertion") in {"clipboard_fallback", "insertion_failed"}
                       for row in rows)
        ttk.Label(page, text=f"Most recent {len(rows)} sessions · {failures} need attention").pack(anchor="w", pady=(0, 8))
        columns = ("date", "provider", "app", "duration", "first_text", "first_written", "asr", "final", "insertion", "issue")
        table = ttk.Treeview(page, columns=columns, show="headings", height=13)
        labels = ("Date", "Provider", "Target app", "Audio (s)", "First text (ms)",
                  "First inserted (ms)", "ASR after stop (ms)", "Final (ms)", "Insertion", "Issue")
        widths = (130, 80, 110, 60, 85, 100, 110, 75, 105, 100)
        for column, label, width in zip(columns, labels, widths):
            table.heading(column, text=label)
            table.column(column, width=width, minwidth=65, stretch=column in {"app", "issue"})
        table.tag_configure("failed", background="#ffe5e5")
        table.tag_configure("recovered", background="#fff3cd")
        for row in rows:
            insertion = row.get("insertion", "—")
            failed = bool(row.get("error")) or insertion == "insertion_failed"
            recovered = insertion == "clipboard_fallback"
            issue = row.get("error") or row.get("failure_stage") or "—"
            values = (row.get("date", "")[:19].replace("T", " "), row.get("provider", "—"),
                      row.get("app", "—"), row.get("recording_seconds", "—"), row.get("first_text_ms", "—"),
                      row.get("first_written_ms", "—"), row.get("asr_after_stop_ms", "—"),
                      row.get("final_after_stop_ms", "—"), insertion, issue)
            table.insert("", "end", values=values,
                         tags=("failed",) if failed else (("recovered",) if recovered else ()))
        table_scroll = ttk.Scrollbar(page, orient="horizontal", command=table.xview)
        table.configure(xscrollcommand=table_scroll.set)
        table_scroll.pack(side="bottom", fill="x")
        table.pack(side="top", fill="both", expand=True)
        controls = ttk.Frame(page)
        controls.pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text="Copy privacy-safe report", command=lambda: self._copy_diagnostics(rows)).pack(side="left")
        ttk.Label(controls, text="Report excludes transcripts and audio").pack(side="left", padx=10)

    def _copy_diagnostics(self, rows):
        report = json.dumps({"app": "SayStride for Windows", "sessions": rows}, ensure_ascii=False, indent=2)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(report)
            self.root.update_idletasks()
            self.status.set("Privacy-safe diagnostics copied to clipboard")
        except tk.TclError:
            self.status.set("Could not copy diagnostics to clipboard")

    def _set_mic(self, value):
        self.recorder.close()
        self.config["microphone"] = None if value == "Default" else int(value.split(":", 1)[0])
        self.recorder.device = self.config["microphone"]
        save(self.config)

    def _save_key(self):
        self.config["cloud_api_key"] = self.key_entry.get().strip()
        save(self.config)
        self.status.set("Cloud key saved")
        self._refresh_provider_status()

    def _set_provider(self, value):
        self._set("dictation_provider", value)
        self._refresh_provider_status()

    def _refresh_provider_status(self):
        if not hasattr(self, "provider_status"):
            return
        if self.config["dictation_provider"] == "deepgram" and deepgram_key():
            self.provider_status.set(f"Active live provider: Deepgram {self.config['deepgram_model']} · cloud")
        elif self.config["dictation_provider"] == "deepgram":
            self.provider_status.set("Active live provider: Local · Deepgram key missing, using local fallback")
        else:
            self.provider_status.set(f"Active live provider: Local · Parakeet {self.config['asr_version']}")

    def _set(self, key, value):
        self.config[key] = value
        save(self.config)
        if key in {"asr_version", "dictation_provider"}:
            self._refresh_provider_status()

    def _offer_call(self):
        if self.config["meeting_detect"] and not self.meeting._running and self.state == "idle":
            app = detected_call()
            if app and not self.root.winfo_viewable():
                self.status.set(f"{app} is open · Meeting notes can be started from the tray")
        self.root.after(30000, self._offer_call)

    def _poll(self):
        latest_cloud = None
        try:
            for _ in range(30):
                kind, value = self.events.get_nowait()
                if kind == "down": self._down(value)
                elif kind == "up": self._up()
                elif kind == "cancel": self._cancel()
                elif kind == "started": self._pill("Listening…")
                elif kind == "live":
                    if self.state == "recording": self._pill("Listening…")
                elif kind == "cloud_live":
                    latest_cloud = value
                elif kind == "level": self._waveform(value)
                elif kind == "result": self._result(*value)
                elif kind == "error": self.status.set(value); self._pill(value); self.root.after(4000, self.pill.withdraw)
                elif kind == "diagnostic":
                    try:
                        self.store.record_diagnostic(value)
                    except OSError:
                        LOG.warning("Could not save dictation diagnostic", exc_info=True)
                elif kind == "hotkey_error": self.status.set(value)
                elif kind == "context":
                    target, before, after = value
                    if self.state in {"recording", "stop_on_release"} and target == self.target:
                        self.before, self.after = before, after
                elif kind == "download_progress":
                    name, done, total = value
                    self.models_status.set(f"{name}: {done / 1e6:.0f} / {total / 1e6:.0f} MB" if total else f"{name}: {done / 1e6:.0f} MB")
                elif kind == "download_done":
                    self.download_active = None
                    self.status.set(f"Downloaded {value}")
                    self._refresh_models()
                elif kind == "download_error":
                    self.download_active = None
                    self.status.set(value)
                    self.models_hint.set("Download failed. Check your connection and try again; partial downloads can resume.")
                    self._refresh_models()
                elif kind == "meeting": self._refresh_meetings()
                elif kind == "meeting_stopped": self.meeting_button.configure(text="Start meeting notes")
                elif kind == "toggle_meeting": self._toggle_meeting()
                elif kind == "open": self.root.deiconify(); self.root.lift()
                elif kind == "quit": self._quit(); return
        except queue.Empty:
            pass
        if latest_cloud and self.state == "recording":
            self._show_live(latest_cloud)
        self.root.after(50, self._poll)

    def _down(self, mode):
        if self.state != "idle":
            return
        self.record_mode = mode
        self.target, self.target_title, self.target_app = foreground()
        self.before, self.after = "", ""
        if self.config["field_context"]:
            threading.Thread(target=self._read_context, args=(self.target,), daemon=True,
                             name="saystride-context").start()
        pair = self.config["app_tones"].get(self.target_app, self.config["default_tones"])
        self.tone = pair["tone"]
        self.pressed_at = time.monotonic()
        self.started_at = self.pressed_at
        self.first_text_at = None
        self.first_written_at = None
        self.live_updates = 0
        self.live_rewrites = 0
        self.toggled = False
        self.frozen_at, self.frozen_text, self.live_text = 0, "", ""
        with self.cloud_lock:
            self.cloud_buffer = [] if self.config["dictation_provider"] == "deepgram" and deepgram_key() else None
        terminal_apps = {"windowsterminal.exe", "wt.exe", "openconsole.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe"}
        self.live_append_mode = self.target_app.lower() in terminal_apps
        self.live_enabled = bool(self.config["live_text"] and (
            self.live_append_mode or self.target_app not in self.config["live_text_excluded"]))
        self.live_stop.clear()
        self.live_thread = None
        self.state = "recording"
        self.status.set(("Hands-free recording · Esc to stop" if mode == "toggle" else "Listening · release F8 to stop")
                        + f" · {self.tone} · {self.target_app}")
        def start():
            try:
                self.recorder.start()
                self.events.put(("started", None))
                if self.cloud_buffer is not None:
                    try:
                        stream = DeepgramStream(deepgram_key(), self.config,
                                               lambda text: self.events.put(("cloud_live", text)))
                        with self.cloud_lock:
                            if self.state == "recording":
                                for chunk in self.cloud_buffer:
                                    stream.feed(chunk)
                                self.deepgram = stream
                            else:
                                stream.close()
                            self.cloud_buffer = None
                    except Exception:
                        LOG.warning("Deepgram connection unavailable; using local speech", exc_info=True)
                        self.events.put(("error", "Deepgram unavailable; using local speech"))
                        with self.cloud_lock:
                            self.cloud_buffer = None
                elif self.config["dictation_provider"] == "deepgram":
                    self.events.put(("error", "Deepgram key missing from root .env; using local speech"))
                if self.state == "recording" and self.deepgram is None:
                    self.live_thread = threading.Thread(target=self._live_loop, daemon=True)
                    self.live_thread.start()
            except Exception as exc:
                self.events.put(("diagnostic", {
                    "provider": "local",
                    "requested_provider": self.config.get("dictation_provider", "local"),
                    "app": self.target_app,
                    "recording_seconds": 0,
                    "insertion": "not_attempted",
                    "failure_stage": "microphone_start",
                    "error": type(exc).__name__,
                }))
                self.events.put(("error", f"Microphone could not start: {exc}"))
                self.events.put(("result", (None, None, None)))
        threading.Thread(target=start, daemon=True).start()

    def _send_cloud_audio(self, samples):
        with self.cloud_lock:
            if self.cloud_buffer is not None:
                self.cloud_buffer.append(samples)
            elif self.deepgram is not None:
                self.deepgram.feed(samples)

    def _show_live(self, text):
        App._write_live(self, text)
        self._pill("Listening…")

    def _write_live(self, text):
        if not text or text == self.live_text:
            return
        if self.first_text_at is None:
            self.first_text_at = time.monotonic()
        if self.live_enabled and not modifiers_down():
            candidate = text
            if getattr(self, "live_append_mode", False):
                if " " not in text:
                    candidate = ""
                else:
                    candidate = text.rsplit(" ", 1)[0] + " "
            append_mode = getattr(self, "live_append_mode", False)
            written = ((revise_append_live(self.target, self.live_text, candidate)
                        if candidate and candidate != self.live_text else False)
                       if append_mode else replace_live(self.target, self.live_text, text))
            if written:
                if self.first_written_at is None:
                    self.first_written_at = time.monotonic()
                self.live_updates += 1
                if append_mode:
                    self.live_rewrites += int(bool(self.live_text) and not candidate.startswith(self.live_text))
                    self.live_text = candidate
                else:
                    self.live_rewrites += int(bool(self.live_text) and not text.startswith(self.live_text))
                    self.live_text = text
            elif (candidate or not append_mode) and foreground()[0] != self.target:
                self.live_enabled = False

    def _read_context(self, target):
        try:
            before, after = field_context()
            self.events.put(("context", (target, before, after)))
        except Exception:
            LOG.debug("Field context unavailable", exc_info=True)

    def _up(self):
        if self.state in {"recording", "stop_on_release"} and self.record_mode == "hold":
            self._finish()

    def _cancel(self):
        if self.state not in {"recording", "stop_on_release"}:
            return
        if self.record_mode == "toggle":
            self._finish()
            return
        self.state = "idle"
        self.live_stop.set()
        self.recorder.stop()
        with self.cloud_lock:
            self.cloud_buffer = None
        if self.deepgram is not None:
            self.deepgram.close()
            self.deepgram = None
        if self.live_text:
            if getattr(self, "live_append_mode", False):
                replace_append_live(self.target, self.live_text, "")
            else:
                replace_live(self.target, self.live_text, "")
        self.toggled = False
        self.pill.withdraw()
        self.status.set("Cancelled")

    def _live_loop(self):
        while not self.live_stop.wait(float(getattr(self, "config", {}).get("live_decode_interval", .45))):
            if self.state not in {"recording", "stop_on_release"}:
                return
            samples = self.recorder.snapshot()
            if len(samples) < 16000:
                continue
            try:
                if len(samples) - self.frozen_at > 8 * 16000:
                    start, end = self.frozen_at + 5 * 16000, self.frozen_at + int(7.5 * 16000)
                    points = range(start, min(end, len(samples) - 3200), 1600)
                    cut = min(points, key=lambda i: float(np.mean(samples[i:i + 3200] ** 2)))
                    self.frozen_text = (self.frozen_text + " " + self.speech.transcribe(samples[self.frozen_at:cut], live=True)).strip()
                    self.frozen_at = cut
                    if self.live_stop.is_set():
                        return
                partial = self.speech.transcribe(samples[self.frozen_at:], live=True)
                if self.live_stop.is_set():
                    return
                text = (self.frozen_text + " " + partial).strip()
                if text:
                    App._write_live(self, text)
                    self.events.put(("live", text))
            except Exception as exc:
                LOG.debug("Live transcript unavailable: %s", exc)

    def _finish(self):
        self.state = "processing"
        self.stopped_at = time.monotonic()
        self.toggled = False
        self.live_stop.set()
        self._pill("Transcribing…")
        def worker():
            provider = "local"
            stage = "recording"
            seconds = 0.0
            try:
                audio = self.recorder.stop()
                seconds = len(audio) / 16000
                with self.cloud_lock:
                    self.cloud_buffer = None
                if self.live_thread and self.live_thread is not threading.current_thread():
                    self.live_thread.join(timeout=5)
                if seconds < .2 or float(np.max(np.abs(audio))) < 1e-5:
                    raise ValueError("Microphone gave silence")
                raw = ""
                stage = "transcription"
                if self.deepgram is not None:
                    try:
                        raw = self.deepgram.finish()
                        provider = "deepgram"
                    except Exception:
                        LOG.warning("Deepgram final unavailable; using local full-clip recognition", exc_info=True)
                        self.events.put(("error", "Deepgram final unavailable; using local speech"))
                    finally:
                        self.deepgram = None
                if not raw:
                    raw = self.speech.transcribe(audio)
                asr_at = time.monotonic()
                if not raw:
                    raise ValueError("No speech was recognized")
                stage = "cleanup"
                examples = {tone: self.store.examples(tone) for tone in self.config["tones"]}
                final = self.polisher.polish(raw, self.tone, examples, window=self.target_title,
                                             before=self.before, after=self.after, seconds=seconds)
                final_at = time.monotonic()
                clip = None
                if self.config["keep_clips"]:
                    from uuid import uuid4
                    clip = f"{uuid4()}.wav"
                    (CLIPS_DIR / clip).write_bytes(wav_bytes(audio))
                metrics = {"first_text_ms": round((self.first_text_at - self.started_at) * 1000) if self.first_text_at else None,
                           "first_written_ms": round((self.first_written_at - self.started_at) * 1000) if self.first_written_at else None,
                           "asr_after_stop_ms": round((asr_at - self.stopped_at) * 1000),
                           "final_after_stop_ms": round((final_at - self.stopped_at) * 1000),
                           "peak": round(float(np.max(np.abs(audio))), 3),
                           "provider": provider, "live_updates": self.live_updates,
                           "live_rewrites": self.live_rewrites}
                self.events.put(("result", (raw, final, (seconds, clip, metrics))))
            except Exception as exc:
                self.events.put(("diagnostic", {
                    "provider": provider,
                    "requested_provider": self.config.get("dictation_provider", "local"),
                    "app": self.target_app,
                    "recording_seconds": round(seconds, 2),
                    "first_text_ms": round((self.first_text_at - self.started_at) * 1000) if self.first_text_at else None,
                    "first_written_ms": round((self.first_written_at - self.started_at) * 1000) if self.first_written_at else None,
                    "live_updates": self.live_updates, "live_rewrites": self.live_rewrites,
                    "insertion": "not_attempted", "failure_stage": stage,
                    "error": type(exc).__name__,
                }))
                self.events.put(("error", str(exc)))
                self.events.put(("result", (None, None, None)))
        threading.Thread(target=worker, daemon=True, name="saystride-dictation").start()

    def _result(self, raw, final, meta):
        self.state = "idle"
        self.pill.withdraw()
        if raw is None:
            return
        wait_for_modifiers()
        insertion = "live_replaced" if self.live_text else "typed"
        if self.live_text:
            append_mode = getattr(self, "live_append_mode", False)
            pasted = (replace_append_live(self.target, self.live_text, final)
                      if append_mode else replace_live(self.target, self.live_text, final))
            if not pasted:
                insertion = "clipboard_fallback" if self._copy_recovery_text(final) else "insertion_failed"
                self.status.set("Text saved to history and clipboard; live text could not be finalized"
                                if insertion == "clipboard_fallback" else
                                "Text saved to history; live insertion and clipboard recovery failed")
        else:
            prefix = " " if self.before and self.before[-1:].isalnum() and final[:1].isalnum() else ""
            pasted = paste(prefix + final, self.target)
            if not pasted:
                insertion = "clipboard_fallback" if self._copy_recovery_text(final) else "insertion_failed"
                self.status.set("Text saved to history and clipboard; insertion was blocked"
                                if insertion == "clipboard_fallback" else
                                "Text saved to history; insertion and clipboard recovery failed")
        metrics = meta[2] if len(meta) > 2 else {}
        try:
            self.store.record_diagnostic({
                "provider": metrics.get("provider", "local"),
                "requested_provider": self.config.get("dictation_provider", "local"),
                "app": self.target_app,
                "recording_seconds": round(meta[0], 2),
                "first_text_ms": metrics.get("first_text_ms"),
                "first_written_ms": metrics.get("first_written_ms"),
                "asr_after_stop_ms": metrics.get("asr_after_stop_ms"),
                "final_after_stop_ms": metrics.get("final_after_stop_ms"),
                "live_updates": metrics.get("live_updates", 0),
                "live_rewrites": metrics.get("live_rewrites", 0),
                "insertion": insertion,
                "error": None if pasted else "insertion_failed",
            })
        except OSError:
            LOG.warning("Could not save dictation diagnostic", exc_info=True)
        self.store.remember(raw=raw, text=final, tone=self.tone, app=self.target_app,
                            seconds=meta[0], clip=meta[1], metrics=meta[2] if len(meta) > 2 else None,
                            paste_sent=pasted)
        self._refresh_history()
        if pasted:
            peak = meta[2].get("peak") if len(meta) > 2 else None
            mic_note = " · microphone very quiet" if peak is not None and peak < .02 else (
                " · microphone clipped" if peak is not None and peak >= .98 else "")
        if pasted:
            self.status.set(f"Pasted {len(final.split())} words · {meta[0]:.1f} s{mic_note}")

    def _copy_recovery_text(self, text):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
        except tk.TclError:
            LOG.warning("Could not copy recovery text to clipboard", exc_info=True)
            return False
        return True

    def _quit(self):
        self.live_stop.set()
        self.hotkey.stop()
        if self.meeting._running:
            self.meeting.stop()
        self.recorder.close()
        self.polisher.stop()
        self.tray.stop()
        self.root.destroy()
