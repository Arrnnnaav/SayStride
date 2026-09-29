param([string]$DataDir = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($DataDir) {
    $full = [IO.Path]::GetFullPath($DataDir)
    New-Item -ItemType Directory -Path $full -Force | Out-Null
    $account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $full /grant:r "${account}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not grant private data-folder access.' }
    & icacls.exe $full /inheritance:r | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not remove inherited data-folder access.' }
    Set-Content -LiteralPath (Join-Path $PSScriptRoot 'data-dir.txt') -Value $full -NoNewline
}
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { py -3.12 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& .\.venv\Scripts\python.exe -m saystride --selftest
if ($LASTEXITCODE -ne 0) { throw 'Self-test failed.' }
Write-Host 'Setup complete. Run .\start.ps1'
