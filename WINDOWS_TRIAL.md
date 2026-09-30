# SayStride Windows trial

## Who to test with

Start with Windows users who dictate long prompts and notes into AI coding agents, VS Code, and terminals. Test technical names and command-line terms from their actual work. Wispr Flow already serves developers, so the evidence to seek is reliable live insertion and useful offline dictation on ordinary Windows laptops.

Do not train Qwen yet. First label what was spoken, what Parakeet or Deepgram heard, and what cleanup wrote. Add recurring names and terms to the Dictionary, then compare the same clips again. Qwen cannot recover words that speech recognition never heard reliably; it may also rewrite exact code tokens. Consider training only after a consented, labeled dataset shows a persistent cleanup problem that prompts and deterministic corrections cannot solve.

## Trial on another laptop

1. Build `SayStride-Setup-X.Y.Z.exe` with `make-installer.ps1` or download the **Windows build** artifact from GitHub Actions. Run it on Windows 10/11.
2. Confirm the Start Menu shortcut, Installed apps uninstall entry, app version, microphone status, and automatic Parakeet setup with visible progress. Interrupt the download once and confirm retry works. Confirm existing user history survives reinstall.
3. Disconnect the internet after setup. Dictate into Notepad and VS Code. Confirm local dictation still completes. VS Code uses final paste by default; live replacement there is currently excluded.
4. Test the same phrases in Word, Notepad, VS Code, PowerShell, Windows Terminal, and a browser text field. In each, note first visible text, final text, focus errors, duplicates, and clipboard recovery. Test an elevated terminal separately.
5. Repeat on at least three machines: an 8 GB CPU-only laptop, a midrange laptop, and a GPU-equipped machine. Record Windows version, CPU, RAM, microphone, provider, and whether optional Qwen cleanup was used.

## Metrics

| Metric | Definition | Where to measure |
| --- | --- | --- |
| TTFVT | Key press to first text actually written in the target field | `first_written_ms` in local diagnostics |
| First recognition | Key press to first ASR text | `first_text_ms` |
| Final delivery | Key release/Esc to final text reaching target or clipboard | `final_delivery_ms` |
| Total latency | Key press to final delivery | `total_ms` |
| Accuracy | Word error rate against a human transcription; lower is better | `benchmark_asr.py` with corrected clips |
| Technical terms | Exact term mentions recovered / expected mentions | `benchmark_asr.py --terms` |
| Insertion reliability | Successful target insertion / attempted sessions | `measure.py sessions` and the app matrix above |
| Resource impact | Peak and spike in process-tree RAM, CPU, and available NVIDIA VRAM | `measure.py resources` |
| Privacy | Outbound bytes and destinations in Local and Deepgram modes | Windows network capture during each mode |
| Setup | Installer bytes, installed bytes, model download bytes/time, first successful dictation | Install log and stopwatch |

Use p50 and p95 for latency; keep audio duration and hardware class beside every result. A replay benchmark measures recognition time on completed clips. It does **not** measure TTFVT or insertion into another app. A transcript disagreement between providers is not an accuracy score until a person supplies the spoken-word reference.

## Commands

```powershell
# Open Settings > Keep dictation clips for evaluation before recording consented samples.
# In History > Correct selected, add what was actually spoken as the reference.
.\.venv\Scripts\python.exe benchmark_asr.py --limit 50 --terms developer-terms.txt
.\.venv\Scripts\python.exe benchmark_asr.py --score-existing --terms developer-terms.txt
.\.venv\Scripts\python.exe measure.py sessions

# With SayStride.exe running, in another PowerShell window:
$saystridePid = (Get-Process SayStride | Select-Object -First 1).Id
.\.venv\Scripts\python.exe measure.py resources --pid $saystridePid --seconds 60
```

Put one name or technical term per line in `developer-terms.txt`. The file and detailed benchmark report can contain private project names and transcriptions; keep them out of the public repository. The resource sampler includes SayStride child processes, such as optional local cleanup. NVIDIA VRAM is reported only when `nvidia-smi` exposes per-process data; otherwise use Windows GPU Process Memory counters or a vendor profiler and label the result unavailable.

For a comparison with Wispr Flow and Windows voice typing, use the same consented phrases, laptop, microphone, and reference transcript. Record final outputs manually and run the same WER calculation. Their products are separate applications, so SayStride cannot automatically capture their internal timings. Use video or screen observation for first visible text.

## Release gate

Before calling the installer a public release, complete one clean install on another Windows laptop, one interrupted-model-download recovery, one offline dictation, and the app insertion matrix. Publish measured results with laptop specifications and sample counts. Keep the source repository and release asset public; do not include `.env`, model files, audio clips, or personal data in the release.
