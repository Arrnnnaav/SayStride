"""Run the app or one of its diagnostic commands."""
from __future__ import annotations

import argparse
import io
import logging
import sys
import time
import unittest
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
import requests

from .config import APP_DIR, CLIPS_DIR, deepgram_key, load
from .deepgram import transcribe_file
from .models import Polisher, Speech, download_model
from .rules import word_error_rate
from .store import Store


def main() -> int:
    parser = argparse.ArgumentParser(description="SayStride for Windows")
    parser.add_argument("--selftest", action="store_true", help="run rules and storage checks without models")
    parser.add_argument("--eval", action="store_true", help="replay corrected clips and report word error rate")
    parser.add_argument("--compare-asr", type=int, metavar="N", help="compare local and Deepgram on N saved clips")
    parser.add_argument("--context", action="store_true", help="show focused field context")
    parser.add_argument("--polishtest", metavar="TEXT", help="run one text sample through the cleanup pipeline")
    parser.add_argument("--notestest", action="store_true", help="summarize a sample meeting")
    parser.add_argument("--mictest", action="store_true", help="record three seconds and show input level")
    parser.add_argument("--download", choices=["parakeet-v2", "parakeet-v3", "qwen"])
    args = parser.parse_args()
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=APP_DIR / "saystride.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.selftest:
        tests = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "tests"
        suite = unittest.defaultTestLoader.discover(str(tests))
        report = io.StringIO()
        passed = unittest.TextTestRunner(stream=report, verbosity=2).run(suite).wasSuccessful()
        (APP_DIR / "selftest.txt").write_text(report.getvalue(), encoding="utf-8")
        if sys.stdout:
            print(report.getvalue())
        return 0 if passed else 1
    if args.download:
        print(download_model(args.download, lambda done, total: print(f"\r{done / 1e6:.0f}/{total / 1e6:.0f} MB", end="", flush=True)))
        return 0
    if args.context:
        from .windows import field_context, foreground
        print("Window:", foreground()[1])
        before, after = field_context()
        print("Before cursor:", repr(before))
        print("After cursor:", repr(after))
        return 0
    if args.mictest:
        print("Available microphones:")
        print(sd.query_devices())
        print("Recording three seconds...")
        data = sd.rec(3 * 16000, samplerate=16000, channels=1, dtype="float32")
        sd.wait()
        print("Peak:", float(np.max(np.abs(data))))
        return 0 if float(np.max(np.abs(data))) > 1e-5 else 1
    config = load()
    store = Store(config)
    if args.polishtest:
        print(Polisher(config).polish(args.polishtest, config["default_tones"]["tone"],
                                      {tone: store.examples(tone) for tone in config["tones"]}))
        return 0
    if args.eval:
        speech, polish = Speech(config), Polisher(config)
        rows = [x for x in store.history if (x.get("reference_final") or x.get("correction") or x.get("reference_raw"))
                and x.get("clip") and (CLIPS_DIR / x["clip"]).exists()]
        if not rows:
            print("No labeled clips yet. In History > Correct selected, enter what you actually said and the desired final text.")
            return 0
        local_config = {**config, "cloud_enabled": False}
        speech = Speech(local_config)
        for item in rows:
            data, rate = sf.read(CLIPS_DIR / item["clip"], dtype="float32")
            if rate != 16000:
                print("Skipped clip at unexpected sample rate:", item["clip"])
                continue
            heard = speech.transcribe(data)
            now = polish.polish(heard, item["tone"], {tone: store.examples(tone) for tone in config["tones"]})
            raw_ref = item.get("reference_raw")
            final_ref = item.get("reference_final") or item.get("correction")
            raw_score = f"raw WER {word_error_rate(raw_ref, heard):.1%}" if raw_ref else "raw WER unlabeled"
            shipped_score = f"shipped final WER {word_error_rate(final_ref, item['text']):.1%}" if final_ref else "shipped final WER unlabeled"
            now_score = f"now final WER {word_error_rate(final_ref, now):.1%}" if final_ref else "now final WER unlabeled"
            print(f"{item['date'][:16]}  {raw_score}  "
                  f"{shipped_score}  {now_score}  "
                  f"paste sent {item.get('paste_sent', 'unknown')}  latency {item.get('metrics', {})}")
        return 0
    if args.compare_asr is not None:
        key = deepgram_key()
        if not key:
            print("DEEPGRAM or DEEPGRAM_API_KEY is missing from the root .env or environment.")
            return 1
        local = Speech({**config, "cloud_enabled": False})
        rows = [x for x in store.history if x.get("clip") and (CLIPS_DIR / x["clip"]).exists() and x.get("seconds", 0) >= 2]
        for item in rows[:max(0, args.compare_asr)]:
            audio, rate = sf.read(CLIPS_DIR / item["clip"], dtype="float32")
            if rate != 16000:
                continue
            t = time.monotonic()
            local_text = local.transcribe(audio)
            local_ms = round((time.monotonic() - t) * 1000)
            t = time.monotonic()
            try:
                cloud_text = transcribe_file(audio, key, config)
            except (requests.RequestException, KeyError, ValueError) as exc:
                print(f"{item['date'][:16]} Deepgram request failed: {type(exc).__name__}")
                continue
            cloud_ms = round((time.monotonic() - t) * 1000)
            reference = item.get("reference_raw")
            print(f"{item['date'][:16]} {item['seconds']:.1f}s | local {local_ms}ms | Deepgram {cloud_ms}ms")
            print(" local:   ", local_text)
            print(" Deepgram:", cloud_text)
            if reference:
                print(f" raw WER: local {word_error_rate(reference, local_text):.1%}, "
                      f"Deepgram {word_error_rate(reference, cloud_text):.1%}")
            else:
                print(" raw WER: needs a human reference in History > Correct selected")
        return 0
    if args.notestest:
        sample = "[0:03] Them: We ship the pricing page on Thursday. Alex sends the reel Friday.\n" \
                 "[0:15] You: I will review the reel Friday and book the venue Wednesday."
        title, notes = Polisher(config).summarize_meeting(sample)
        print(title, "\n\n", notes)
        return 0
    from .app import App
    App().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
