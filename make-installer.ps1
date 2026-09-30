param([string]$Version = '0.1.0', [string]$Python = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Version must be three numeric components, for example 0.1.0.' }
$compiler = (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
if (-not $compiler) { $compiler = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe' }
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Install Inno Setup 6 or build on GitHub Actions windows-2022.' }
$runtimePython = if ($Python) { $Python } else { '.\.venv\Scripts\python.exe' }
& .\build.ps1 -OutputDir dist-final -Python $runtimePython
& $compiler "/DAppVersion=$Version" 'SayStride.iss'
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
$result = Get-Item -LiteralPath "release\SayStride-Setup-$Version.exe"
$sha = (Get-FileHash -LiteralPath $result.FullName -Algorithm SHA256).Hash
Write-Host "Installer: $($result.FullName)"
Write-Host "Size: $([math]::Round($result.Length / 1MB, 1)) MiB"
Write-Host "SHA-256: $sha"
