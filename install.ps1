param(
    [string]$SourceDir = (Join-Path $PSScriptRoot 'dist-final\SayStride'),
    [switch]$Startup
)
$ErrorActionPreference = 'Stop'

$source = (Resolve-Path -LiteralPath $SourceDir).Path
$exe = Join-Path $source 'SayStride.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "Built app not found: $exe. Run .\build.ps1 -OutputDir dist-final first." }

$install = Join-Path $env:LOCALAPPDATA 'SayStride'
New-Item -ItemType Directory -Path $install -Force | Out-Null
Get-ChildItem -LiteralPath $source -Force | Where-Object Name -ne 'tools' |
    Copy-Item -Destination $install -Recurse -Force
$installedTools = Join-Path $install 'tools'
if (-not (Test-Path -LiteralPath $installedTools)) {
    Copy-Item -LiteralPath (Join-Path $source 'tools') -Destination $install -Recurse -Force
}

$shell = New-Object -ComObject WScript.Shell
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\SayStride.lnk'
$shortcut = $shell.CreateShortcut($startMenu)
$shortcut.TargetPath = Join-Path $install 'SayStride.exe'
$shortcut.WorkingDirectory = $install
$shortcut.Description = 'SayStride voice dictation'
$shortcut.Save()

if ($Startup) {
    $startupPath = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\SayStride.lnk'
    $startupShortcut = $shell.CreateShortcut($startupPath)
    $startupShortcut.TargetPath = Join-Path $install 'SayStride.exe'
    $startupShortcut.WorkingDirectory = $install
    $startupShortcut.Description = 'Start SayStride with Windows'
    $startupShortcut.Save()
}

Write-Host "Installed SayStride to $install"
Write-Host "Start Menu shortcut created. Startup enabled: $Startup"
