"""Summarize local dictation sessions or sample a running SayStride process."""
from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
import subprocess
import time
from pathlib import Path

import psutil

from saystride.config import APP_DIR


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(fraction * len(values)) - 1)]


def summarize(rows: list[dict]) -> dict:
    fields = ("first_text_ms", "first_written_ms", "asr_after_stop_ms", "final_after_stop_ms",
              "final_delivery_ms", "total_ms")
    report = {"sessions": len(rows),
              "inserted": sum(row.get("insertion") in {"typed", "live_replaced"} for row in rows),
              "clipboard_fallback": sum(row.get("insertion") == "clipboard_fallback" for row in rows),
              "failed": sum(row.get("insertion") == "insertion_failed" for row in rows)}
    for field in fields:
        values = [row[field] for row in rows if isinstance(row.get(field), (int, float)) and row[field] >= 0]
        report[field] = {"n": len(values), "p50": percentile(values, .5), "p95": percentile(values, .95)}
    return report


def nvidia_vram_mb(pids: set[int]) -> int | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=True)
        rows = [line.split(",") for line in result.stdout.splitlines()]
        values = [int(parts[1].strip()) for parts in rows
                  if len(parts) == 2 and parts[0].strip().isdigit()
                  and int(parts[0].strip()) in pids and parts[1].strip().isdigit()]
        return sum(values) if values else 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def sample_resources(pid: int, seconds: float, interval: float) -> dict:
    process = psutil.Process(pid)
    process.cpu_percent(None)
    samples = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not process.is_running():
            break
        active = [process] + process.children(recursive=True)
        try:
            rss = sum(item.memory_info().rss for item in active if item.is_running())
            cpu = sum(item.cpu_percent(None) for item in active if item.is_running())
        except psutil.Error:
            continue
        samples.append({"rss_mb": round(rss / 1024**2, 1),
                        "cpu_percent": round(cpu, 1),
                        "nvidia_vram_mb": nvidia_vram_mb({item.pid for item in active})})
        time.sleep(interval)
    if not samples:
        raise RuntimeError("No resource samples captured")
    vram = [row["nvidia_vram_mb"] for row in samples if row["nvidia_vram_mb"] is not None]
    return {"pid": pid, "samples": len(samples), "interval_seconds": interval,
            "rss_start_mb": samples[0]["rss_mb"],
            "rss_peak_mb": max(row["rss_mb"] for row in samples),
            "rss_spike_mb": round(max(row["rss_mb"] for row in samples) - samples[0]["rss_mb"], 1),
            "cpu_peak_percent": max(row["cpu_percent"] for row in samples),
            "nvidia_vram_peak_mb": max(vram) if vram else None,
            "gpu_note": None if vram else "NVIDIA per-process VRAM unavailable; inspect GPU Process Memory on this PC"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sessions = commands.add_parser("sessions", help="aggregate transcript-free local diagnostics")
    sessions.add_argument("--input", type=Path, default=APP_DIR / "dictation_diagnostics.json")
    resources = commands.add_parser("resources", help="sample CPU, RAM, and NVIDIA VRAM by process ID")
    resources.add_argument("--pid", type=int, required=True)
    resources.add_argument("--seconds", type=float, default=60)
    resources.add_argument("--interval", type=float, default=.5)
    args = parser.parse_args()
    if args.command == "sessions":
        rows = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            parser.error("Diagnostics must be a JSON list")
        rows = [row for row in rows if isinstance(row, dict)]
        report = {"all": summarize(rows)}
        for provider in sorted({row.get("provider", "unknown") for row in rows if isinstance(row, dict)}):
            report[provider] = summarize([row for row in rows if isinstance(row, dict)
                                          and row.get("provider", "unknown") == provider])
    else:
        if args.seconds <= 0 or args.interval <= 0:
            parser.error("Sampling duration and interval must be positive")
        report = sample_resources(args.pid, args.seconds, args.interval)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
