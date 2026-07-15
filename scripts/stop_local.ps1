[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$LogsDir = Join-Path $RootDir "logs"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  停止所有本地服务" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

foreach ($pair in @(
    @((Join-Path $LogsDir "backend.pid"),  "后端"),
    @((Join-Path $LogsDir "celery.pid"),   "Celery"),
    @((Join-Path $LogsDir "frontend.pid"), "前端")
)) {
    $f = $pair[0]; $name = $pair[1]
    if (Test-Path $f) {
        $pid_ = Get-Content $f -ErrorAction SilentlyContinue
        if ($pid_) {
            $p = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
            if ($p) {
                Write-Host "  [STOP] $name (PID=$pid_)" -ForegroundColor Yellow
                try { Stop-Process -Id $pid_ -Force -ErrorAction Stop } catch {}
                # 杀子进程
                Get-CimInstance Win32_Process -Filter "ParentProcessId=$pid_" -ErrorAction SilentlyContinue |
                    ForEach-Object {
                        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
                    }
            }
            Remove-Item $f -Force -ErrorAction SilentlyContinue
        }
    }
}

# 兜底按端口清理
foreach ($port in @(5000, 5173, 8000)) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        try {
            $pName = (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName
            Write-Host "  [PORT] 端口 $port 上的进程 $($c.OwningProcess) ($pName)" -ForegroundColor Yellow
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        } catch {}
    }
}

Write-Host ""
Write-Host "  [DONE] 已停止所有服务" -ForegroundColor Green
Write-Host ""
