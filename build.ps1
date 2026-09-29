param([string]$OutputDir = 'dist')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = if (Test-Path -LiteralPath '.buildvenv\Scripts\python.exe') { '.\.buildvenv\Scripts\python.exe' } else { '.\.venv\Scripts\python.exe' }
& $python -m pip show pyinstaller *> $null
if ($LASTEXITCODE -ne 0) { & $python -m pip install pyinstaller }
& $python -m pip install 'websocket-client>=1.8'
if ($LASTEXITCODE -ne 0) { throw 'WebSocket dependency installation failed.' }
& $python -c "import comtypes.client; comtypes.client.GetModule('UIAutomationCore.dll')"
if ($LASTEXITCODE -ne 0) { throw 'UI Automation setup failed.' }
& $python -m PyInstaller --noconfirm --windowed --onedir --name SayStride `
    --distpath $OutputDir `
    --add-data 'tests;tests' `
    --collect-all sherpa_onnx --collect-all faster_whisper --collect-all ctranslate2 `
    --hidden-import win32clipboard --hidden-import win32gui --hidden-import win32process --hidden-import comtypes.gen.UIAutomationClient --hidden-import unittest.mock `
    run.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
$bundle = Join-Path $OutputDir 'SayStride'
Copy-Item -LiteralPath 'README.md' -Destination (Join-Path $bundle 'README.md') -Force
if (Test-Path -LiteralPath 'data-dir.txt') { Copy-Item -LiteralPath 'data-dir.txt' -Destination (Join-Path $bundle 'data-dir.txt') -Force }
if (Test-Path -LiteralPath 'tools\llama\llama-server.exe') {
    $tools = Join-Path $bundle 'tools\llama'
    New-Item -ItemType Directory -Path $tools -Force | Out-Null
    Copy-Item -Path 'tools\llama\*' -Destination $tools -Force
}
Write-Host "Built $bundle\SayStride.exe"
