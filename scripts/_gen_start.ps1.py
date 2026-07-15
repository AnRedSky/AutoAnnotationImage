"""Generate start_local.ps1 - ASCII-only version (avoid PS5 encoding issues)"""
import os

script = r'''# ============================================================
#  Graduation Thesis Project - Windows Local One-Click Startup
#  ============================================================
#  Usage:
#    .\scripts\start_local.ps1              # default: backend + frontend
#    .\scripts\start_local.ps1 -All         # backend + celery + frontend
#    .\scripts\start_local.ps1 -Backend     # backend only
#    .\scripts\start_local.ps1 -Frontend    # frontend only
#    .\scripts\start_local.ps1 -Celery      # celery only
#    .\scripts\start_local.ps1 -Test        # start + run E2E
#    .\scripts\start_local.ps1 -Stop        # stop all
#    .\scripts\start_local.ps1 -Restart     # restart
#    .\scripts\start_local.ps1 -Status      # show status
# ============================================================

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ---- Paths ----
$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir      = Split-Path -Parent $ScriptDir
$BackendDir   = Join-Path $RootDir "backend"
$FrontendDir  = Join-Path $RootDir "frontend"
$LogsDir      = Join-Path $RootDir "logs"
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir | Out-Null }

$BackendPidFile  = Join-Path $LogsDir "backend.pid"
$CeleryPidFile   = Join-Path $LogsDir "celery.pid"
$FrontendPidFile = Join-Path $LogsDir "frontend.pid"

# ---- Log functions ----
function Log-Info($msg) { Write-Host ("  [INFO] " + $msg) -ForegroundColor Yellow }
function Log-OK($msg)   { Write-Host ("  [OK]   " + $msg) -ForegroundColor Green }
function Log-Err($msg)  { Write-Host ("  [ERR]  " + $msg) -ForegroundColor Red }
function Log-Section($msg) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ("  " + $msg) -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
}

# ---- Utility: Test Port ----
function Test-Port {
    param($Host_, $Port_)
    $tcp = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $tcp.BeginConnect($Host_, $Port_, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(500)
        if (-not $ok) { $tcp.Close(); return $false }
        $tcp.EndConnect($iar)
        $tcp.Close()
        return $true
    } catch { return $false }
}

# ---- Utility: Stop Process Tree ----
function Stop-Tree {
    param($ParentPid)
    try { Stop-Process -Id $ParentPid -Force -ErrorAction SilentlyContinue } catch {}
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$ParentPid" -ErrorAction SilentlyContinue |
        ForEach-Object {
            try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
        }
}

# ---- Utility: Get Port Listener ----
function Get-PortInfo {
    param($Port)
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
}

# ---- Check Dependencies ----
function Check-Deps {
    Log-Section "Checking service dependencies"
    $script:mysqlOK = Test-Port "127.0.0.1" 3306
    $script:redisOK = Test-Port "127.0.0.1" 6379
    $script:minioOK = Test-Port "127.0.0.1" 9000

    $script:nodeV = ""
    $script:nodeOK = $false
    try { $script:nodeV = (node -v) 2>$null; if ($script:nodeV) { $script:nodeOK = $true } } catch {}

    $script:pyV = ""
    $script:pyOK = $false
    try { $script:pyV = (python --version) 2>$null; if ($script:pyV) { $script:pyOK = $true } } catch {}

    if ($script:mysqlOK) { Log-OK "MySQL  : 127.0.0.1:3306" } else { Log-Err "MySQL  : 3306 NOT running" }
    if ($script:redisOK) { Log-OK "Redis  : 127.0.0.1:6379" } else { Log-Info "Redis  : 6379 NOT running (Celery will degrade)" }
    if ($script:minioOK) { Log-OK "MinIO  : 127.0.0.1:9000" } else { Log-Info "MinIO  : 9000 NOT running (will use local FS)" }
    if ($script:nodeOK) { Log-OK ("Node   : " + $script:nodeV) } else { Log-Err "Node   : NOT installed" }
    if ($script:pyOK) { Log-OK ("Python : " + $script:pyV) } else { Log-Err "Python : NOT installed" }
}

# ---- Start Backend ----
function Start-Backend {
    Log-Section "Starting backend FastAPI"
    $venvPy = Join-Path $BackendDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
        Log-Err ("venv not found: " + $venvPy)
        return $false
    }
    $log = Join-Path $LogsDir "backend.log"
    $errLog = Join-Path $LogsDir "backend.err.log"
    if (Test-Path $log) { Remove-Item $log -Force }
    if (Test-Path $errLog) { Remove-Item $errLog -Force }
    Log-Info ("Log: " + $log)
    Log-Info "Cmd: uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload"

    $cmdLine = "/k cd /d """ + $BackendDir + """ & .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload"
    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList $cmdLine -WindowStyle Normal -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError $errLog
    $proc.Id | Out-File $BackendPidFile -Encoding ascii
    Log-OK ("Backend PID=" + $proc.Id)

    $ok = $false
    for ($i = 0; $i -lt 20; $i++) {
        if (Test-Port "127.0.0.1" 5000) { $ok = $true; break }
        Start-Sleep -Seconds 1
    }
    if ($ok) { Log-OK "Backend listening on :5000" } else { Log-Err "Backend startup timeout (20s)" }
    return $ok
}

# ---- Start Celery ----
function Start-Celery {
    Log-Section "Starting Celery Worker"
    if (-not $script:redisOK) {
        Log-Info "Redis not available, skipping Celery"
        return $false
    }
    $celery = Join-Path $BackendDir ".venv\Scripts\celery.exe"
    if (-not (Test-Path $celery)) {
        Log-Err ("celery not found: " + $celery)
        return $false
    }
    $log = Join-Path $LogsDir "celery.log"
    if (Test-Path $log) { Remove-Item $log -Force }
    Log-Info ("Log: " + $log)
    $cmdLine = "/k cd /d """ + $BackendDir + """ & .\.venv\Scripts\celery.exe -A app.workers.celery_app worker --loglevel=info --pool=solo"
    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList $cmdLine -WindowStyle Normal -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError ($log + ".err")
    $proc.Id | Out-File $CeleryPidFile -Encoding ascii
    Log-OK ("Celery PID=" + $proc.Id)
    Start-Sleep -Seconds 2
    return $true
}

# ---- Start Frontend ----
function Start-Frontend {
    Log-Section "Starting frontend Vue"
    if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
        Log-Info "node_modules not found, installing..."
        $installCmd = "/c cd /d """ + $FrontendDir + """ & npm install"
        $ip = Start-Process -FilePath "cmd.exe" -ArgumentList $installCmd -Wait -PassThru -NoNewWindow
        if ($ip.ExitCode -ne 0) {
            Log-Err "npm install failed, please run manually"
            return $false
        }
    }
    $log = Join-Path $LogsDir "frontend.log"
    if (Test-Path $log) { Remove-Item $log -Force }
    Log-Info ("Log: " + $log)
    $cmdLine = "/k cd /d """ + $FrontendDir + """ & npm run dev"
    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList $cmdLine -WindowStyle Normal -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError ($log + ".err")
    $proc.Id | Out-File $FrontendPidFile -Encoding ascii
    Log-OK ("Frontend PID=" + $proc.Id)

    $ok = $false
    for ($i = 0; $i -lt 20; $i++) {
        if (Test-Port "127.0.0.1" 5173) { $ok = $true; break }
        Start-Sleep -Seconds 1
    }
    if ($ok) { Log-OK "Frontend listening on :5173" } else { Log-Info "Frontend startup slow, check log later" }
    return $true
}

# ---- Stop All ----
function Stop-All {
    Log-Section "Stopping all services"
    foreach ($pair in @(
        @($BackendPidFile, "Backend"),
        @($CeleryPidFile,  "Celery"),
        @($FrontendPidFile, "Frontend")
    )) {
        $f = $pair[0]
        $name = $pair[1]
        if (Test-Path $f) {
            $pid_ = Get-Content $f -ErrorAction SilentlyContinue
            if ($pid_) {
                $p = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
                if ($p) {
                    Log-Info ("Stopping " + $name + " (PID=" + $pid_ + ")")
                    Stop-Tree $pid_
                }
                Remove-Item $f -Force -ErrorAction SilentlyContinue
            }
        }
    }
    foreach ($port in @(5000, 5173, 8000)) {
        $conns = Get-PortInfo $port
        foreach ($c in $conns) {
            $pName = (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName
            Log-Info ("Cleanup port " + $port + " pid " + $c.OwningProcess + " (" + $pName + ")")
            try { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
    Log-OK "All services stopped"
}

# ---- Show Status ----
function Show-Status {
    Log-Section "Service status"
    foreach ($pair in @(
        @("Backend", 5000, $BackendPidFile),
        @("Frontend", 5173, $FrontendPidFile),
        @("Celery", 0, $CeleryPidFile)
    )) {
        $name = $pair[0]
        $port = $pair[1]
        $f = $pair[2]
        $running = $false
        $pid_ = ""
        if ($port -gt 0) {
            $c = Get-PortInfo $port
            if ($c) { $running = $true; $pid_ = $c.OwningProcess }
        }
        if (-not $running -and (Test-Path $f)) {
            $pid_ = Get-Content $f -ErrorAction SilentlyContinue
            $p = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
            if ($p) { $running = $true }
        }
        if ($running) {
            Log-OK ($name + "  : RUNNING (PID=" + $pid_ + ")")
        } else {
            Log-Info ($name + "  : NOT running")
        }
    }
    Log-Info ("Log dir: " + $LogsDir)
}

# ---- Main Flow ----
$StartBackend  = $false
$StartFrontend = $false
$StartCelery   = $false
$RunTest       = $false
$DoStop        = $false
$DoRestart     = $false
$DoStatus      = $false

if ($args.Count -eq 0) { $StartBackend = $true; $StartFrontend = $true }

foreach ($a in $args) {
    switch ($a.ToLower()) {
        "-all"      { $StartBackend = $true; $StartCelery = $true; $StartFrontend = $true }
        "-backend"  { $StartBackend = $true }
        "-frontend" { $StartFrontend = $true }
        "-celery"   { $StartCelery = $true }
        "-test"     { $RunTest = $true; $StartBackend = $true; $StartFrontend = $true }
        "-stop"     { $DoStop = $true }
        "-restart"  { $DoRestart = $true; $StartBackend = $true; $StartFrontend = $true }
        "-status"   { $DoStatus = $true }
        "-h"        { Write-Host "Usage: start_local.ps1 [-All|-Backend|-Frontend|-Celery|-Test|-Stop|-Restart|-Status]"; exit 0 }
        default     { Log-Info ("Unknown arg: " + $a) }
    }
}

if ($DoStop)    { Stop-All; exit 0 }
if ($DoStatus)  { Show-Status; exit 0 }
if ($DoRestart) { Stop-All; Start-Sleep -Seconds 2 }

Check-Deps
if ($StartBackend)  { Start-Backend }
if ($StartCelery)   { Start-Celery }
if ($StartFrontend) { Start-Frontend }

Start-Sleep -Seconds 1

Log-Section "Startup complete"
Log-OK "Backend API     : http://127.0.0.1:5000/docs"
Log-OK "Frontend        : http://127.0.0.1:5173"
Log-OK "Health check    : http://127.0.0.1:5000/api/health"
Log-Info ("Log dir        : " + $LogsDir)
Log-Info "Stop services   : .\scripts\stop_local.bat"

if ($RunTest) {
    & (Join-Path $ScriptDir "e2e_test.ps1")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
'''

# Write with UTF-8 (no BOM) to avoid Windows code page issues
# But ASCII-only content, so no encoding issue
with open('start_local.ps1', 'w', encoding='ascii', newline='') as f:
    f.write(script.replace('\n', '\r\n'))
print("Written successfully, size:", len(script))
print("File encoding: ASCII (no Chinese to avoid PS5 issues)")
