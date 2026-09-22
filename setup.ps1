<#
.SYNOPSIS
    One-time setup for live-translator. Safe to re-run, and what you run after
    moving this folder somewhere else (a venv holds absolute paths, so it has to
    be rebuilt rather than copied).

.DESCRIPTION
    Creates the virtual environment, installs Python dependencies, and checks that
    Ollama has the suggestion model. Does not install Python or Ollama themselves -
    it tells you the winget command if either is missing.
#>

param(
    [switch]$Force,          # delete and rebuild the virtual environment
    [string]$SuggestModel    # model to pull, default: read from config.toml
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

function Say($text, $colour = "Gray") { Write-Host $text -ForegroundColor $colour }
function Ok($text)   { Write-Host "  ok   $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  warn $text" -ForegroundColor Yellow }
function Bad($text)  { Write-Host "  FAIL $text" -ForegroundColor Red }

Say "live-translator setup" "Cyan"
Say ("=" * 60)

# config
# config.toml ships with the repo, so a fresh clone already has one. An older
# copy pins the NVIDIA settings, and on an AMD or Intel machine that means a
# GPU-sized model on the CPU, which is slower than real time
$config = Join-Path $root "config.toml"
if (-not (Test-Path $config)) {
    Warn "no config.toml found - the app will use built-in defaults"
} else {
    $text = Get-Content $config -Raw
    $before = $text
    $text = $text -replace '(?m)^\s*device\s*=\s*"cuda"\s*(#.*)?$', 'device = "auto"'
    $text = $text -replace '(?m)^\s*model\s*=\s*"(large-v3-turbo|large-v3|large-v2|medium)"\s*(#.*)?$', 'model = "auto"'
    $text = $text -replace '(?m)^\s*compute_type\s*=\s*"float16"\s*(#.*)?$', 'compute_type = "auto"'
    if ($text -ne $before) {
        Copy-Item $config "$config.backup" -Force
        Set-Content -Path $config -Value $text -Encoding utf8 -NoNewline
        Ok "config.toml updated to pick hardware automatically (old copy: config.toml.backup)"
    } else {
        Ok "config.toml looks fine"
    }
}

# python
$python = $null
foreach ($candidate in @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
)) {
    if (Test-Path $candidate) { $python = $candidate; break }
}
if (-not $python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    # Skip the Microsoft Store stub, which is not a real interpreter
    if ($cmd -and $cmd.Source -notlike "*WindowsApps*") { $python = $cmd.Source }
}
if (-not $python) {
    Bad "Python 3.10-3.12 not found."
    Say ""
    Say "Install it with:" "Cyan"
    Say "  winget install --id Python.Python.3.11 --scope user"
    exit 1
}
Ok "python - $python"

# venv
$venv = Join-Path $root ".venv"
if ($Force -and (Test-Path $venv)) {
    Say "  removing existing virtual environment..."
    Remove-Item -Recurse -Force $venv
}
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Say "  creating virtual environment..."
    & $python -m venv $venv
    if (-not $?) { Bad "venv creation failed"; exit 1 }
}
$venvPython = Join-Path $venv "Scripts\python.exe"
Ok "virtual environment - $venv"

# packages
Say "  installing dependencies (this downloads ~1 GB of CUDA libraries once)..."
& $venvPython -m pip install --upgrade --quiet pip wheel
& $venvPython -m pip install --quiet -r (Join-Path $root "requirements.txt")
if (-not $?) { Bad "dependency install failed"; exit 1 }
Ok "dependencies installed"

# ollama
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) {
    $fallback = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path $fallback) { $ollama = $fallback }
}

if (-not $ollama) {
    Warn "Ollama not found - translation and suggestions will be unavailable."
    Say ""
    Say "Install it, then re-run this script:" "Cyan"
    Say "  winget install --id Ollama.Ollama"
} else {
    Ok "ollama - $ollama"

    if (-not $SuggestModel) {
        $configPath = Join-Path $root "config.toml"
        if (Test-Path $configPath) {
            $match = Select-String -Path $configPath -Pattern '^\s*model\s*=\s*"([^"]+)"' -AllMatches
            # [suggest].model is the second `model =` key in the file ([asr] comes first)
            if ($match.Matches.Count -ge 1) {
                $SuggestModel = $match[-1].Matches[0].Groups[1].Value
            }
        }
    }
    if (-not $SuggestModel) { $SuggestModel = "qwen3:4b" }

    $installed = & $ollama list 2>$null
    if ($installed -match [regex]::Escape($SuggestModel)) {
        Ok "model $SuggestModel already present"
    } else {
        Say "  pulling $SuggestModel (a few GB)..."
        & $ollama pull $SuggestModel
        if ($?) { Ok "model $SuggestModel pulled" } else { Warn "pull failed - run: ollama pull $SuggestModel" }
    }
}

# verify
Say ""
Say ("=" * 60)
Say "Running preflight check..." "Cyan"
$env:PYTHONPATH = Join-Path $root "src"
& $venvPython (Join-Path $root "check.py")
$checkResult = $LASTEXITCODE

Say ""
if ($checkResult -eq 0) {
    Say "Setup complete. Start it with:  .\run.ps1" "Green"
    Say "Whisper downloads its model (~1.6 GB) on the first run." "DarkGray"
} else {
    Say "Setup finished with problems - see the failures above." "Yellow"
}
exit $checkResult
