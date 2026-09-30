# SayStride for Windows

SayStride is a Windows desktop dictation app that types into the focused app. It supports local speech recognition and optional Deepgram cloud transcription.

## What it does

- **F8:** hold to dictate; release to finish.
- **F9:** start hands-free dictation; press **Esc** to finish.
- Types live text into Word, Notepad, browsers, and supported fields. Terminals and VS Code use safer append or final paste paths by default.
- Uses direct terminal insertion with append and revision handling for PowerShell, Command Prompt, and Windows Terminal.
- Keeps local dictation history, dictionary replacements, tones, and memory.
- Formats spoken sequences such as “first”, “second”, and “third” as numbered lists during final cleanup.

## Requirements

- Windows 10 or 11
- Python 3.11 or newer when running from source; the installer includes the runtime
- A working microphone

Optional: an NVIDIA GPU can improve local model speed.

## Quick start

```powershell
git clone https://github.com/Arrnnnaav/SayStride.git
cd SayStride
.\setup.ps1
.\start.ps1
```

On first launch, SayStride checks the microphone and automatically downloads and verifies Parakeet v2 if it is missing. The Models page shows progress and offers retry. Local dictation then runs without sending microphone audio to a cloud service. Qwen cleanup remains an optional, larger download.

## Dictation controls

1. Click where text should appear.
2. Hold **F8** while speaking, then release it; or press **F9** once for hands-free dictation.
3. For F9 mode, press **Esc** to finish.

Use **Settings** to enable or disable live text, the floating status indicator, and cleanup mode.

### Terminals and elevated apps

SayStride and the target app must run at the same Windows privilege level. If you dictate into an Administrator PowerShell or terminal, start SayStride as Administrator too:

1. Close SayStride.
2. Right-click `SayStride.exe` and select **Run as administrator**.
3. Open the elevated terminal and dictate normally.

For normal terminals, run both normally. Do not run two copies of SayStride at once.

## Speech providers

### Local

Local Parakeet is the default. Audio stays on the computer. It requires the downloaded speech model and is best for offline use.

### Deepgram

Deepgram provides optional cloud transcription. For an installed app, create `%APPDATA%\SayStride\.env`; for a source checkout, create `.env` beside this README:

```dotenv
DEEPGRAM_API_KEY=your_key_here
```

Restart SayStride, open **Settings**, and choose Deepgram. The active provider is shown there. Never commit `.env`.

## Cleanup modes

- **Fast** is the default. It applies immediate deterministic cleanup and list formatting.
- **Quality** uses the optional local Qwen cleanup model for more editing after dictation finishes.

To enable Quality mode, download the Qwen model from **Models**. The optional llama.cpp runtime can be downloaded with:

```powershell
.\.venv\Scripts\python.exe .\download_llama.py
```

## Build and install

Build a distributable Windows app from source:

```powershell
.\build.ps1 -OutputDir dist-final
```

Install that build for the current user:

```powershell
.\install.ps1 -SourceDir .\dist-final\SayStride
```

Start SayStride when you sign in:

```powershell
.\install.ps1 -Startup
```

To create a normal per-user installer with Start Menu shortcuts and an uninstaller, install Inno Setup 6 and run:

```powershell
.\make-installer.ps1 -Version 0.1.0
```

The result is `release\SayStride-Setup-0.1.0.exe`. It installs without requiring Python or administrator rights and launches SayStride, which sets up Parakeet on first run with an internet connection. App data and models live in `%APPDATA%\SayStride`, outside the install folder. The installer does not contain downloaded models, optional Qwen or llama.cpp, API keys, or the developer's `data-dir.txt`.

The GitHub Actions **Windows build** workflow creates an installer artifact on manual runs. A `vX.Y.Z` tag publishes that installer in this repository's Releases.

## Tests and benchmarks

Run the test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

See [WINDOWS_TRIAL.md](WINDOWS_TRIAL.md) for the second-laptop trial, latency, reliability, resource, and privacy measurements.

Compare providers with the same audio clips:

```powershell
.\.venv\Scripts\python.exe .\benchmark_asr.py --help
```

## Data and privacy

SayStride stores its configuration, models, and dictation history locally. Local transcription keeps audio on the device. When Deepgram is selected, dictated audio is sent to Deepgram for transcription under that provider’s terms.

Generated builds, downloaded models, local data, and API keys are excluded from this repository.
