# ============================================================
#  一键部署脚本 - Windows PowerShell
# ============================================================
#  自动化流程:
#    1) 检查 .env (不存在则从 .env.example 复制, 并生成密钥)
#    2) 验证 Docker / Compose
#    3) 构建镜像 (首次或 --rebuild)
#    4) 启动所有服务 (docker compose up -d)
#    5) 等待服务 healthy
#    6) 运行部署后验证
#
#  用法:
#    .\scripts\start_docker.ps1                  # 默认启动
#    .\scripts\start_docker.ps1 -Rebuild          # 强制重新构建镜像
#    .\scripts\start_docker.ps1 -SkipBuild        # 跳过构建
#    .\scripts\start_docker.ps1 -Status           # 仅查看状态
#    .\scripts\start_docker.ps1 -Stop             # 停止所有服务
#    .\scripts\start_docker.ps1 -Logs             # 查看日志
#    .\scripts\start_docker.ps1 -Verify           # 仅运行验证
#    .\scripts\start_docker.ps1 -Reset            # 停止并删除卷 (重置数据库)
# ============================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir   = Split-Path -Parent $ScriptDir
$EnvFile   = Join-Path $RootDir ".env"
$EnvExample = Join-Path $RootDir ".env.example"
$ComposeFile = Join-Path $RootDir "docker-compose.yml"

# 颜色
function Write-Step($msg) {
    Write-Host ""
    Write-Host "▶ $msg" -ForegroundColor Cyan
}
function Write-OK($msg) { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "  ℹ $msg" -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host "  ✗ $msg" -ForegroundColor Red }
function Write-Header($msg) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Magenta
    Write-Host "  $msg" -ForegroundColor Magenta
    Write-Host "============================================================" -ForegroundColor Magenta
}

# 参数解析
$Rebuild = $Rebuild -or $args -contains "-Rebuild" -or $args -contains "--rebuild"
$SkipBuild = $SkipBuild -or $args -contains "-SkipBuild" -or $args -contains "--skip-build"
$StatusOnly = $StatusOnly -or $args -contains "-Status" -or $args -contains "--status"
$StopOnly = $StopOnly -or $args -contains "-Stop" -or $args -contains "--stop"
$ShowLogs = $ShowLogs -or $args -contains "-Logs" -or $args -contains "--logs"
$VerifyOnly = $VerifyOnly -or $args -contains "-Verify" -or $args -contains "--verify"
$Reset = $Reset -or $args -contains "-Reset" -or $args -contains "--reset"

# Banner
Write-Header "图像自动标注系统 - 一键部署"
Write-Info "项目根: $RootDir"
Write-Info "Compose: $ComposeFile"
Write-Info "Env:     $EnvFile"

# ============================================================
# 步骤 1: 准备 .env
# ============================================================
Write-Step "1) 准备 .env 文件"
if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Write-Info ".env 不存在, 从 .env.example 复制"
        Copy-Item $EnvExample $EnvFile
    } else {
        Write-Err ".env 和 .env.example 都不存在, 无法继续"
        exit 1
    }
    Write-Info "正在生成强随机密钥..."
    & python "$ScriptDir\gen_secrets.py" --write --env-file "$EnvFile"
    if ($LASTEXITCODE -ne 0) {
        Write-Err "密钥生成失败, 请手动运行: python scripts/gen_secrets.py --write"
        exit 1
    }
    Write-OK "已生成 .env (含强随机密钥)"
} else {
    Write-OK ".env 已存在"
    # 检查是否还有 CHANGEME 占位符
    $changeme = Select-String -Path $EnvFile -Pattern "CHANGEME" -SimpleMatch -ErrorAction SilentlyContinue
    if ($changeme) {
        Write-Info "检测到 CHANGEME 占位符, 正在替换..."
        & python "$ScriptDir\gen_secrets.py" --write --env-file "$EnvFile"
        if ($LASTEXITCODE -ne 0) {
            Write-Err "密钥生成失败"
            exit 1
        }
        Write-OK "已替换占位符"
    }
}

# ============================================================
# 步骤 2: 检查 Docker
# ============================================================
Write-Step "2) 检查 Docker 环境"
try {
    $dockerVer = docker --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "docker 命令不可用, 请先安装 Docker Desktop"
        exit 1
    }
    Write-OK "$dockerVer"
} catch {
    Write-Err "未找到 docker, 请先安装 Docker Desktop"
    exit 1
}

try {
    $composeVer = docker compose version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "docker compose 不可用, 请升级到 Docker Desktop 4.x+"
        exit 1
    }
    Write-OK "$composeVer"
} catch {
    Write-Err "未找到 docker compose"
    exit 1
}

# ============================================================
# 分支: 仅查看状态
# ============================================================
if ($StatusOnly) {
    Write-Step "服务状态"
    docker compose -f $ComposeFile --env-file $EnvFile ps
    Write-Info "URLs:"
    Write-Info "  前端:    http://localhost:8080"
    Write-Info "  API:     http://localhost:8000"
    Write-Info "  Swagger: http://localhost:8000/docs"
    Write-Info "  MinIO:   http://localhost:9001 (Console)"
    exit 0
}

# ============================================================
# 分支: 停止服务
# ============================================================
if ($StopOnly) {
    Write-Step "停止所有服务"
    docker compose -f $ComposeFile --env-file $EnvFile down
    Write-OK "已停止"
    exit 0
}

# ============================================================
# 分支: 查看日志
# ============================================================
if ($ShowLogs) {
    Write-Step "查看日志 (Ctrl+C 退出)"
    docker compose -f $ComposeFile --env-file $EnvFile logs -f --tail=100
    exit 0
}

# ============================================================
# 分支: 重置 (停止 + 删除卷)
# ============================================================
if ($Reset) {
    Write-Step "重置 (停止 + 删除卷)"
    $conf = Read-Host "  确认要删除所有数据? (yes/no)"
    if ($conf -eq "yes") {
        docker compose -f $ComposeFile --env-file $EnvFile down -v
        Write-OK "已删除所有卷, 数据库已重置"
    } else {
        Write-Info "已取消"
    }
    exit 0
}

# ============================================================
# 分支: 仅验证
# ============================================================
if ($VerifyOnly) {
    Write-Step "运行部署验证"
    & python "$ScriptDir\verify_deployment.py" --skip-build --env-file $EnvFile
    exit $LASTEXITCODE
}

# ============================================================
# 步骤 3: 构建镜像
# ============================================================
if (-not $SkipBuild) {
    Write-Step "3) 构建 Docker 镜像"
    if ($Rebuild) {
        Write-Info "强制重建 (--no-cache)..."
        docker compose -f $ComposeFile --env-file $EnvFile build --no-cache
    } else {
        Write-Info "增量构建 (仅修改过的层)..."
        docker compose -f $ComposeFile --env-file $EnvFile build
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Err "镜像构建失败"
        exit 1
    }
    Write-OK "镜像构建完成"
} else {
    Write-Step "3) 跳过构建 (--SkipBuild)"
}

# ============================================================
# 步骤 4: 启动所有服务
# ============================================================
Write-Step "4) 启动所有服务 (docker compose up -d)"
docker compose -f $ComposeFile --env-file $EnvFile up -d
if ($LASTEXITCODE -ne 0) {
    Write-Err "启动失败, 请查看日志: docker compose logs"
    exit 1
}
Write-OK "所有服务已启动"

# ============================================================
# 步骤 5: 等待服务 healthy
# ============================================================
Write-Step "5) 等待服务 healthy (最多 90s)"
$maxWait = 90
$startTime = Get-Date
$services = @("mysql", "redis", "minio", "api", "frontend")
$allHealthy = $false
while (((Get-Date) - $startTime).TotalSeconds -lt $maxWait) {
    $unhealthy = @()
    foreach ($svc in $services) {
        $status = docker compose -f $ComposeFile --env-file $EnvFile ps --format json 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue | Where-Object { $_.Service -eq $svc }
        if ($status) {
            $s = $status.State
            if ($s -ne "running") {
                $unhealthy += "$svc=$s"
            }
        } else {
            $unhealthy += "$svc=missing"
        }
    }
    if ($unhealthy.Count -eq 0) {
        $allHealthy = $true
        break
    }
    Write-Host "  等待: $($unhealthy -join ', ')" -ForegroundColor DarkGray
    Start-Sleep -Seconds 5
}
if ($allHealthy) {
    Write-OK "所有核心服务 healthy"
} else {
    Write-Info "部分服务未 healthy, 但启动流程已结束"
    Write-Info "查看状态: docker compose ps"
}

# ============================================================
# 步骤 6: 部署后验证
# ============================================================
Write-Step "6) 部署后验证"
Write-Info "等待 API 启动 (15s)..."
Start-Sleep -Seconds 15
& python "$ScriptDir\verify_deployment.py" --skip-build --env-file $EnvFile
$verifyRc = $LASTEXITCODE

# ============================================================
# 总结
# ============================================================
Write-Header "部署完成"
Write-Info "URLs:"
Write-Host "  前端:    http://localhost:8080" -ForegroundColor Green
Write-Host "  API:     http://localhost:8000" -ForegroundColor Green
Write-Host "  Swagger: http://localhost:8000/docs" -ForegroundColor Green
Write-Host "  MinIO:   http://localhost:9001 (admin / 见 .env)" -ForegroundColor Green
Write-Host ""
Write-Info "常用命令:"
Write-Host "  查看状态: docker compose ps" -ForegroundColor Yellow
Write-Host "  查看日志: docker compose logs -f" -ForegroundColor Yellow
Write-Host "  停止服务: docker compose down" -ForegroundColor Yellow
Write-Host "  重置数据: docker compose down -v" -ForegroundColor Yellow
Write-Host ""

if ($verifyRc -eq 0) {
    Write-OK "部署 + 验证全部成功 ✓"
} else {
    Write-Info "部署成功, 但验证发现问题 (退出码 $verifyRc), 请检查日志"
}

exit $verifyRc
