"""Dictation history, corrections and meeting files."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .config import APP_DIR, MEETINGS_DIR, save
from .rules import learn_names, learn_signals, merge_dictionary

DIAGNOSTIC_FIELDS = {"id", "date", "provider", "requested_provider", "app", "recording_seconds",
                     "first_text_ms", "first_written_ms", "asr_after_stop_ms", "final_after_stop_ms",
                     "final_delivery_ms", "total_ms", "live_updates", "live_rewrites", "insertion",
                     "failure_stage", "error"}


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


class Store:
    def __init__(self, config: dict):
        self.config = config
        path = APP_DIR / "history.json"
        try:
            self.history = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.history = []

    def record_diagnostic(self, event: dict) -> None:
        """Keep bounded, transcript-free metadata for support and reliability work."""
        item = {"id": str(uuid4()), "date": datetime.now().astimezone().isoformat()}
        item.update({key: event[key] for key in DIAGNOSTIC_FIELDS if key in event})
        path = APP_DIR / "dictation_diagnostics.json"
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                rows = []
        except (OSError, ValueError):
            rows = []
        rows.insert(0, item)
        _write(path, rows[:100])

    def diagnostics(self, limit: int = 20) -> list[dict]:
        """Read recent allowlisted diagnostic metadata for the in-app viewer."""
        try:
            rows = json.loads((APP_DIR / "dictation_diagnostics.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(rows, list):
            return []
        return [{key: value for key, value in row.items() if key in DIAGNOSTIC_FIELDS}
                for row in rows if isinstance(row, dict)][:max(0, min(limit, 100))]

    def remember(self, *, raw: str, text: str, tone: str, app: str, seconds: float,
                 clip: str | None = None, metrics: dict | None = None, paste_sent: bool | None = None) -> dict:
        item = dict(id=str(uuid4()), date=datetime.now().astimezone().isoformat(), raw=raw, text=text,
                    tone=tone, app=app, seconds=round(seconds, 2), clip=clip, correction=None,
                    metrics=metrics or {}, paste_sent=paste_sent)
        self.history.insert(0, item)
        self.history = self.history[:200]
        _write(APP_DIR / "history.json", self.history)
        return item

    def correct(self, item_id: str, correction: str, reference_raw: str = "") -> None:
        item = next((x for x in self.history if x["id"] == item_id), None)
        if item is None:
            raise KeyError(item_id)
        item["reference_final"] = correction.strip() or None
        item["correction"] = correction.strip() if correction.strip() != item["text"] else None
        item["reference_raw"] = reference_raw.strip() or None
        if item["correction"]:
            names = learn_names(item["raw"], item["correction"])
            self.config["dictionary"] = merge_dictionary(self.config["dictionary"], names)
            signals = learn_signals(item["raw"], item["correction"])
            self.config["learned_signals"] = list(dict.fromkeys(self.config["learned_signals"] + signals))[-20:]
            save(self.config)
        _write(APP_DIR / "history.json", self.history)

    def examples(self, tone: str, limit: int = 3) -> list[tuple[str, str]]:
        rows = [x for x in self.history if x.get("tone") == tone and x.get("correction")]
        return [(x["raw"], x["correction"]) for x in reversed(rows[:limit])]

    def meetings(self) -> list[dict]:
        found = []
        for path in MEETINGS_DIR.glob("*.json"):
            try:
                found.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(found, key=lambda x: x.get("start", ""), reverse=True)

    def save_meeting(self, meeting: dict) -> None:
        _write(MEETINGS_DIR / f"{meeting['id']}.json", meeting)
