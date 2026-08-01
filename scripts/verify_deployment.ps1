# ============================================================
#  部署验证脚本 - PowerShell 包装
# ============================================================
#  委托给 Python 版本 (跨平台, 中文路径友好)
#
#  用法:
#    .\scripts\verify_deployment.ps1                    # 完整验证
#    .\scripts\verify_deployment.ps1 -SkipE2e           # 跳过 E2E
#    .\scripts\verify_deployment.ps1 -Strict            # 严格模式
#    .\scripts\verify_deployment.ps1 -EnvFile backend\.env.prod
#    .\scripts\verify_deployment.ps1 -Report            # 输出报告
# ============================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir   = Split-Path -Parent $ScriptDir

# 优先用 backend venv 的 python, 没有则用系统 python
$PyExe = Join-Path $RootDir "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) {
    $PyExe = "python"
}

# PowerShell 参数转 Python 参数
$PyArgs = @()
$Translated = @()
foreach ($a in $args) {
    $key = $a.ToString()
    switch -Wildcard ($key) {
        "-SkipBuild"   { $Translated += "--skip-build" }
        "-SkipE2e"     { $Translated += "--skip-e2e" }
        "-Strict"      { $Translated += "--strict" }
        "-Report"      { $Translated += "--report" }
        "-EnvFile"     { $Translated += "--env-file"; $expectValue = $true }
        "-ApiPort"     { $Translated += "--api-port"; $expectValue = $true }
        default {
            if ($expectValue) {
                $Translated += $key
                $expectValue = $false
            } else {
                $Translated += $key
            }
        }
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  部署验证脚本 (PowerShell Wrapper)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Root:     $RootDir" -ForegroundColor Gray
Write-Host "  Python:   $PyExe" -ForegroundColor Gray
Write-Host ""

# 调用 Python
& $PyExe (Join-Path $ScriptDir "verify_deployment.py") $Translated
exit $LASTEXITCODE
