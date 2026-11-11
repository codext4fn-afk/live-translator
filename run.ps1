<#
.SYNOPSIS
    Launch live-translator.

.EXAMPLE
    .\run.ps1                       # start the overlay
    .\run.ps1 --check               # verify the install
    .\run.ps1 --list-devices        # show loopback capture devices
    .\run.ps1 --language es         # skip detection, assume Spanish
    .\run.ps1 --no-suggest          # transcript only, skips Ollama
#>

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $python)) {
    Write-Host "No virtual environment found." -ForegroundColor Red
    Write-Host "Run .\setup.ps1 first."
    exit 1
}

# Non-ASCII transcripts must survive the console encoding
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = Join-Path $root "src"

if ($args -contains "--check") {
    & $python (Join-Path $root "check.py")
    exit $LASTEXITCODE
}

# --list-devices and --debug write to the console, so they need a console host
# Everything else is a GUI, so use pythonw to avoid a stray black window behind
# the overlay
$needsConsole = ($args -contains "--list-devices") -or ($args -contains "--debug") -or ($args -contains "--version")
$interpreter = if ($needsConsole -or -not (Test-Path $pythonw)) { $python } else { $pythonw }

& $interpreter "-m" "livetranslator" @args
exit $LASTEXITCODE
