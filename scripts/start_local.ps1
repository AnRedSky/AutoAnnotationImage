# ============================================================
#  Graduation Thesis Project - Windows PowerShell Wrapper
#  ============================================================
#  Thin wrapper that delegates to start_local.py
#  (Python is more reliable for Chinese paths and complex IO)
#
#  Usage:
#    .\start_local.ps1                  # backend
#    .\start_local.ps1 all              # backend + celery + frontend
#    .\start_local.ps1 -All
#    .\start_local.ps1 -Backend
#    .\start_local.ps1 -Frontend
#    .\start_local.ps1 -Celery
#    .\start_local.ps1 -Redis
#    .\start_local.ps1 -Status
#    .\start_local.ps1 -Stop
#    .\start_local.ps1 -Restart
#    .\start_local.ps1 -Doctor
#    .\start_local.ps1 -Test
# ============================================================

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir   = Split-Path -Parent $ScriptDir
$PyExe     = Join-Path $RootDir "backend\.venv\Scripts\python.exe"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Graduation Thesis Project - Local One-Click Startup" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Backend API  : http://127.0.0.1:5000/docs"
Write-Host "  Frontend     : http://127.0.0.1:5173"
Write-Host "  Stop service : scripts\stop_local.bat"
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Map PowerShell-style flags to start_local.py args
$Map = @{
    "-All"      = "all"
    "-Backend"  = "backend"
    "-Frontend" = "frontend"
    "-Celery"   = "celery"
    "-Redis"    = "redis"
    "-Status"   = "status"
    "-Stop"     = "stop"
    "-Restart"  = "restart"
    "-Doctor"   = "doctor"
    "-Test"     = "test"
}

$Translated = @()
if ($args.Count -eq 0) {
    $Translated = @("backend")
} else {
    foreach ($a in $args) {
        $key = $a.ToString()
        if ($Map.ContainsKey($key)) {
            $Translated += $Map[$key]
        } else {
            # Pass through (e.g. unknown flag, numeric repeat)
            $Translated += $key
        }
    }
}

# Check Python venv
if (-not (Test-Path $PyExe)) {
    Write-Host "[ERR] Python venv not found: $PyExe" -ForegroundColor Red
    Write-Host "      Please run: cd backend && uv venv .venv && uv pip install -e ." -ForegroundColor Yellow
    exit 1
}

# Delegate to Python launcher
$joined = $Translated -join " "
Write-Host "  [INFO] Delegating to: python start_local.py $joined" -ForegroundColor Yellow
Write-Host ""

& $PyExe (Join-Path $ScriptDir "start_local.py") $Translated
exit $LASTEXITCODE
