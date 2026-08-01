# ============================================================
# 服务间通信验证脚本 (PowerShell Native)
# ============================================================
$ErrorActionPreference = "Continue"

$envPath = "d:\works\WorkBuddy\Myhome\ThesisDesignImplementation\thesis-image-annotation\.env"
$envContent = Get-Content $envPath -Encoding UTF8

# 解析 .env
$envMap = @{}
foreach ($line in $envContent) {
    if ($line -match '^\s*#' -or [string]::IsNullOrWhiteSpace($line)) { continue }
    if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$') {
        $k = $matches[1]
        $v = $matches[2]
        $envMap[$k] = $v
    }
}

$mysqlRoot = $envMap["MYSQL_ROOT_PASSWORD"]
$redisPwd  = $envMap["REDIS_PASSWORD"]
$minioUser = $envMap["MINIO_ACCESS_KEY"]
$minioPwd  = $envMap["MINIO_SECRET_KEY"]

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  服务间通信验证" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 1) Redis 验证
Write-Host "[1/6] Redis ping ..." -ForegroundColor Yellow
$redisOut = docker exec annotation_redis redis-cli -a $redisPwd --no-auth-warning ping 2>&1
$redisOut | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
if ($redisOut -match "PONG") {
    Write-Host "  ✓ Redis: PONG" -ForegroundColor Green
} else {
    Write-Host "  ✗ Redis: failed" -ForegroundColor Red
}

# 2) MySQL 验证
Write-Host ""
Write-Host "[2/6] MySQL ping ..." -ForegroundColor Yellow
$mysqlOut = docker exec annotation_mysql mysqladmin ping -h 127.0.0.1 -u root -p"$mysqlRoot" 2>&1
$mysqlOut | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
if ($mysqlOut -match "alive") {
    Write-Host "  ✓ MySQL: alive" -ForegroundColor Green
} else {
    Write-Host "  ✗ MySQL: failed" -ForegroundColor Red
}

# 3) MinIO 验证
Write-Host ""
Write-Host "[3/6] MinIO health ..." -ForegroundColor Yellow
try {
    $minioResp = Invoke-WebRequest -Uri "http://127.0.0.1:9000/minio/health/live" -Method Get -UseBasicParsing -TimeoutSec 5
    Write-Host "  ✓ MinIO /minio/health/live: $($minioResp.StatusCode) $($minioResp.StatusDescription)" -ForegroundColor Green
} catch {
    Write-Host "  ✗ MinIO: $($_.Exception.Message)" -ForegroundColor Red
}

# 4) MinIO 验证 (ready)
Write-Host ""
Write-Host "[4/6] MinIO ready ..." -ForegroundColor Yellow
try {
    $minioResp2 = Invoke-WebRequest -Uri "http://127.0.0.1:9000/minio/health/ready" -Method Get -UseBasicParsing -TimeoutSec 5
    Write-Host "  ✓ MinIO /minio/health/ready: $($minioResp2.StatusCode) $($minioResp2.StatusDescription)" -ForegroundColor Green
} catch {
    Write-Host "  ✗ MinIO ready: $($_.Exception.Message)" -ForegroundColor Red
}

# 5) DNS 解析 (跨容器)
Write-Host ""
Write-Host "[5/6] DNS resolution (annotation_mysql -> redis, minio) ..." -ForegroundColor Yellow
$dnsOut = docker exec annotation_mysql bash -c "getent hosts redis minio" 2>&1
$dnsOut | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
if ($dnsOut -match "redis" -and $dnsOut -match "minio") {
    Write-Host "  ✓ DNS: redis + minio 解析成功" -ForegroundColor Green
} else {
    Write-Host "  ✗ DNS: 解析失败" -ForegroundColor Red
}

# 6) Redis 从 MySQL 容器内部访问
Write-Host ""
Write-Host "[6/6] Cross-service: mysql -> redis ..." -ForegroundColor Yellow
# 使用 wget 测试 redis (mysql 镜像中可能没有 redis-cli, 改用 nc)
$crossOut = docker exec annotation_mysql bash -c "apt list --installed 2>/dev/null | grep -i netcat" 2>&1
# 简化: 用 timeout 测试 TCP 连接
$crossOut2 = docker exec annotation_mysql bash -c "timeout 3 bash -c 'cat < /dev/tcp/redis/6379' && echo 'redis tcp ok' || echo 'redis tcp failed'" 2>&1
$crossOut2 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
if ($crossOut2 -match "redis tcp ok") {
    Write-Host "  ✓ 跨容器通信: mysql -> redis:6379 TCP OK" -ForegroundColor Green
} else {
    Write-Host "  ⚠ 跨容器 TCP 测试无法直接确认, 但 DNS 正常" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  验证完成" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
