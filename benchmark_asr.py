"""Replay saved dictation clips through local ASR and Deepgram Nova-3."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf

from saystride.config import APP_DIR, CLIPS_DIR, deepgram_key, load
from saystride.deepgram import transcribe_file
from saystride.models import Speech
from saystride.rules import word_error_rate, words
from saystride.store import Store


def median(values):
    return round(statistics.median(values)) if values else None


def p95(values):
    return sorted(values)[max(0, math.ceil(.95 * len(values)) - 1)] if values else None


def term_counts(reference: str, transcript: str, terms: list[str]) -> tuple[int, int]:
    expected = heard = 0
    original, result = words(reference), words(transcript)
    for term in terms:
        tokens = words(term)
        if not tokens:
            continue
        count = lambda source: sum(source[i:i + len(tokens)] == tokens
                                   for i in range(len(source) - len(tokens) + 1))
        needed = count(original)
        expected += needed
        heard += min(needed, count(result))
    return heard, expected


def score(report: dict, history: list[dict]) -> None:
    references = {item["id"]: item.get("reference_raw") for item in history}
    labeled = []
    for row in report["clips"]:
        reference = references.get(row["history_id"])
        row["local_wer"] = round(word_error_rate(reference, row["local_text"]), 4) if reference else None
        row["deepgram_wer"] = round(word_error_rate(reference, row["deepgram_text"]), 4) if reference else None
        if reference:
            labeled.append((row, len(words(reference))))
    summary = report["summary"]
    terms = report.get("terms", [])
    summary["labeled_clips"] = len(labeled)
    for model in ("local", "deepgram"):
        field = f"{model}_corpus_wer"
        if labeled:
            total_words = sum(length for _, length in labeled)
            summary[field] = round(sum(word_error_rate(references[row["history_id"]], row[f"{model}_text"]) * length
                                       for row, length in labeled) / total_words, 4)
        else:
            summary.pop(field, None)
        term_hits = term_total = 0
        for row, _ in labeled:
            hits, total = term_counts(references[row["history_id"]], row[f"{model}_text"], terms)
            term_hits += hits
            term_total += total
        summary[f"{model}_term_recall"] = round(term_hits / term_total, 4) if term_total else None
    summary["expected_term_mentions"] = term_total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="newest N usable clips; default all")
    parser.add_argument("--score-existing", action="store_true", help="score saved transcripts after labeling; no API calls")
    parser.add_argument("--output", default="", help="report path; defaults to the app data folder")
    parser.add_argument("--terms", type=Path, help="one technical term or proper name per line")
    args = parser.parse_args()
    config = load()
    history = Store(config).history
    path = APP_DIR / "asr_benchmark.json"
    if args.output:
        path = Path(args.output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if args.score_existing:
        report = json.loads(path.read_text(encoding="utf-8"))
        if args.terms:
            report["terms"] = [line.strip() for line in args.terms.read_text(encoding="utf-8").splitlines()
                               if line.strip() and not line.lstrip().startswith("#")]
        score(report, history)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report["summary"], indent=2))
        return 0
    key = deepgram_key()
    if not key:
        parser.error("Set DEEPGRAM in the project root .env first")
    if config["asr_backend"] != "parakeet":
        parser.error("Select the Parakeet backend before comparing it with Deepgram")
    rows = sorted((item for item in history if item.get("clip")
                   and (CLIPS_DIR / item["clip"]).exists() and item.get("seconds", 0) >= 2),
                  key=lambda item: (item.get("date", ""), item["id"]))
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        parser.error("No saved clips of at least two seconds")

    local = Speech({**config, "cloud_enabled": False})
    local._load_parakeet()  # The running app prewarms this model too.
    results = []
    for i, item in enumerate(rows, 1):
        audio, rate = sf.read(CLIPS_DIR / item["clip"], dtype="float32")
        if rate != 16000:
            print(f"{i}/{len(rows)} skipped: unexpected sample rate", flush=True)
            continue
        started = time.perf_counter()
        local_text = local.transcribe(audio)
        local_ms = round((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        try:
            deepgram_text = transcribe_file(audio, key, config)
        except Exception as exc:
            print(f"{i}/{len(rows)} Deepgram failed: {type(exc).__name__}", flush=True)
            continue
        deepgram_ms = round((time.perf_counter() - started) * 1000)
        reference = item.get("reference_raw")
        results.append({
            "history_id": item["id"], "clip": item["clip"], "audio_seconds": round(len(audio) / rate, 3),
            "audio_sha256": hashlib.sha256((CLIPS_DIR / item["clip"]).read_bytes()).hexdigest(),
            "local_ms": local_ms, "deepgram_ms": deepgram_ms,
            "local_rtf": round(local_ms / (len(audio) / rate * 1000), 3),
            "deepgram_rtf": round(deepgram_ms / (len(audio) / rate * 1000), 3),
            "local_text": local_text, "deepgram_text": deepgram_text,
            "disagreement_wer": round(word_error_rate(local_text, deepgram_text), 4),
            "local_wer": round(word_error_rate(reference, local_text), 4) if reference else None,
            "deepgram_wer": round(word_error_rate(reference, deepgram_text), 4) if reference else None,
        })
        print(f"{i}/{len(rows)}  {len(audio) / rate:.1f}s audio  "
              f"local {local_ms}ms  Deepgram {deepgram_ms}ms", flush=True)

    if not results:
        return 1
    summary = {
        "clips": len(results), "audio_seconds": round(sum(r["audio_seconds"] for r in results), 1),
        "local_median_ms": median([r["local_ms"] for r in results]),
        "local_p95_ms": p95([r["local_ms"] for r in results]),
        "deepgram_median_ms": median([r["deepgram_ms"] for r in results]),
        "deepgram_p95_ms": p95([r["deepgram_ms"] for r in results]),
        "local_median_rtf": round(statistics.median(r["local_rtf"] for r in results), 3),
        "deepgram_median_rtf": round(statistics.median(r["deepgram_rtf"] for r in results), 3),
        "local_total_ms": sum(r["local_ms"] for r in results),
        "deepgram_total_ms": sum(r["deepgram_ms"] for r in results),
        "median_disagreement_wer": round(statistics.median(r["disagreement_wer"] for r in results), 4),
        "labeled_clips": sum(r["local_wer"] is not None for r in results),
    }
    terms = ([line.strip() for line in args.terms.read_text(encoding="utf-8").splitlines()
              if line.strip() and not line.lstrip().startswith("#")] if args.terms else [])
    report = {"run_utc": datetime.now(timezone.utc).isoformat(), "clip_order": "date,id ascending",
              "local_model": f"parakeet-{config['asr_version']}",
              "deepgram_model": config["deepgram_model"], "keyterms": config["deepgram_keyterms"],
              "terms": terms, "summary": summary, "clips": results}
    score(report, history)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print("Saved private transcripts and timings to", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
