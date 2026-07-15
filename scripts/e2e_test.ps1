# ============================================================
#  毕业论文项目 - E2E 集成测试脚本
#  ============================================================
#  启动后端后调用, 自动完成:
#    1. 健康检查
#    2. 注册 + 登录
#    3. 创建数据集 + 类别
#    4. 上传图片
#    5. AI 预标注
#    6. 人工标注 / 修正
#    7. 统计接口
#    8. 训练任务（启动 + 进度）
#    9. 模型版本管理
#   10. 导出 (COCO/YOLO/CSV)
#
#  用法:
#    .\scripts\e2e_test.ps1                # 假设后端已在 :5000
#    .\scripts\e2e_test.ps1 -BaseUrl http://127.0.0.1:5000
# ============================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

param(
    [string]$BaseUrl = "http://127.0.0.1:5000",
    [string]$Username = "",
    [string]$Password = "Test123456"
)

if (-not $Username) {
    $Username = "e2e_$((Get-Date).Ticks.ToString().Substring(0,10))"
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir

# ===== 颜色 =====
function Write-Section($msg) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
}
function Write-OK($msg)   { Write-Host "  [PASS] $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "  [INFO] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  [FAIL] $msg" -ForegroundColor Red }

# ===== 全局结果 =====
$script:results = @()
$script:passed = 0
$script:failed = 0

function Test-Step {
    param($Name, $ScriptBlock)
    Write-Host ""
    Write-Host ">> $Name" -ForegroundColor Magenta
    try {
        $r = & $ScriptBlock
        $script:results += @{ name = $Name; ok = $true; data = $r }
        $script:passed++
        Write-OK "通过"
        return $r
    } catch {
        $script:results += @{ name = $Name; ok = $false; err = $_.Exception.Message }
        $script:failed++
        Write-Err "失败: $($_.Exception.Message)"
        return $null
    }
}

# ===== 1. 健康检查 =====
Write-Section "[1/10] 健康检查"
$healthOk = $false
for ($i=0; $i -lt 15; $i++) {
    try {
        $r = Invoke-WebRequest "$BaseUrl/docs" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $healthOk = $true; break }
    } catch {}
    Write-Host "  等待后端 ($($i+1)/15)..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 2
}
if (-not $healthOk) {
    Write-Err "后端未就绪，请先运行 .\scripts\start_local.ps1"
    exit 1
}
Write-OK "后端在 $BaseUrl 运行正常"

# ===== 2. 注册 + 登录 =====
Write-Section "[2/10] 用户注册 / 登录"
Test-Step "注册测试用户 $Username" {
    $body = @{ username = $Username; password = $Password; email = "$Username@test.local"; role = "admin" } | ConvertTo-Json
    Invoke-RestMethod "$BaseUrl/api/auth/register" -Method Post -Body $body -ContentType "application/json"
}

$loginResp = Test-Step "登录获取 token" {
    $body = "username=$Username&password=$Password" -replace "\$", ""
    Invoke-RestMethod "$BaseUrl/api/auth/login" -Method Post -Body $body -ContentType "application/x-www-form-urlencoded"
}

if (-not $loginResp.access_token) {
    Write-Err "登录未返回 token"
    exit 1
}
$script:token = $loginResp.access_token
$script:headers = @{ Authorization = "Bearer $script:token" }
Write-OK "Token 获取成功"

# ===== 3. 创建数据集 + 类别 =====
Write-Section "[3/10] 数据集 CRUD"
$dsResp = Test-Step "创建数据集（3 类别）" {
    $body = @{
        name = "e2e_ds_$((Get-Date).Ticks.ToString().Substring(0,6))"
        description = "E2E test dataset"
        task_type = "classification"
        category_names = @("cat_a", "cat_b", "cat_c")
    } | ConvertTo-Json
    Invoke-RestMethod "$BaseUrl/api/datasets" -Method Post -Body $body -ContentType "application/json" -Headers $script:headers
}
$script:datasetId = $dsResp.id
Write-Info "dataset_id = $script:datasetId"

Test-Step "获取数据集详情" {
    Invoke-RestMethod "$BaseUrl/api/datasets/$script:datasetId" -Method Get -Headers $script:headers
}

Test-Step "列出数据集" {
    Invoke-RestMethod "$BaseUrl/api/datasets" -Method Get -Headers $script:headers
}

$catResp = Test-Step "列出类别" {
    Invoke-RestMethod "$BaseUrl/api/datasets/$script:datasetId/categories" -Method Get -Headers $script:headers
}
$script:catA = $catResp.items[0].id
$script:catB = $catResp.items[1].id
Write-Info "类别: catA=$script:catA, catB=$script:catB"

# ===== 4. 上传图片 =====
Write-Section "[4/10] 图片上传（去重）"
$imgBytes = New-TestPng
$tmpA = New-TempFile
[System.IO.File]::WriteAllBytes($tmpA.FullName, $imgBytes)

# E2E 多文件上传需要 multipart, PowerShell 自带方法不直观, 改用 Invoke-RestMethod 的 -InFile
Test-Step "上传单张 PNG" {
    $curlArgs = @("-s", "-X", "POST", "$BaseUrl/api/images/upload/$script:datasetId", "-H", "Authorization: Bearer $script:token", "-F", "files=@$($tmpA.FullName);type=image/png")
    $r = & curl @curlArgs
    $r | ConvertFrom-Json
}

Test-Step "重复上传应去重" {
    $curlArgs = @("-s", "-X", "POST", "$BaseUrl/api/images/upload/$script:datasetId", "-H", "Authorization: Bearer $script:token", "-F", "files=@$($tmpA.FullName);type=image/png")
    $r = & curl @curlArgs
    $r | ConvertFrom-Json
}

Test-Step "查询图片列表" {
    Invoke-RestMethod "$BaseUrl/api/images/list/$script:datasetId?page=1&page_size=20" -Method Get -Headers $script:headers
}

# ===== 5. AI 预标注 =====
Write-Section "[5/10] AI 自动标注"
Test-Step "启动自动标注 (efficientnet_b0, threshold=0.6)" {
    $body = @{ dataset_id = $script:datasetId; model_name = "efficientnet_b0"; confidence_threshold = 0.6; async_mode = $false } | ConvertTo-Json
    Invoke-RestMethod "$BaseUrl/api/auto-annotate/run" -Method Post -Body $body -ContentType "application/json" -Headers $script:headers
}

Test-Step "查询可用模型" {
    Invoke-RestMethod "$BaseUrl/api/auto-annotate/models" -Method Get -Headers $script:headers
}

# ===== 6. 人工标注 =====
Write-Section "[6/10] 人工确认/修正"
$imgList = Test-Step "取第一张图片 ID" {
    Invoke-RestMethod "$BaseUrl/api/images/list/$script:datasetId?page=1&page_size=1" -Method Get -Headers $script:headers
}
$firstImgId = $imgList.items[0].id
Write-Info "image_id = $firstImgId"

Test-Step "确认标注 (cat_a)" {
    $body = @{ image_id = $firstImgId; label_id = $script:catA; time_spent_ms = 2500; is_confirm = $true } | ConvertTo-Json
    Invoke-RestMethod "$BaseUrl/api/annotation/save" -Method Post -Body $body -ContentType "application/json" -Headers $script:headers
}

Test-Step "获取标注统计" {
    Invoke-RestMethod "$BaseUrl/api/annotation/stats/$script:datasetId" -Method Get -Headers $script:headers
}

# ===== 7. 统计接口 =====
Write-Section "[7/10] 统计接口"
Test-Step "系统总览" {
    Invoke-RestMethod "$BaseUrl/api/stats/overview" -Method Get -Headers $script:headers
}

Test-Step "数据集统计" {
    Invoke-RestMethod "$BaseUrl/api/stats/dataset/$script:datasetId" -Method Get -Headers $script:headers
}

Test-Step "置信度分布" {
    Invoke-RestMethod "$BaseUrl/api/stats/confidence/$script:datasetId" -Method Get -Headers $script:headers
}

Test-Step "标注时间线" {
    Invoke-RestMethod "$BaseUrl/api/stats/timeline/$script:datasetId?days=7" -Method Get -Headers $script:headers
}

Test-Step "标注员效率" {
    Invoke-RestMethod "$BaseUrl/api/stats/annotator-efficiency" -Method Get -Headers $script:headers
}

# ===== 8. 训练任务 =====
Write-Section "[8/10] 模型训练"
Test-Step "启动训练任务（允许失败 - 数据不足时）" {
    try {
        $body = @{ dataset_id = $script:datasetId; base_model = "efficientnet_b0"; model_name = "e2e_v1"; epochs = 1; batch_size = 8 }
        Invoke-RestMethod "$BaseUrl/api/training/start" -Method Post -Headers $script:headers -Body $body
    } catch {
        $_.Exception.Response.StatusCode.value__
    }
}

Test-Step "查询训练进度" {
    Invoke-RestMethod "$BaseUrl/api/training/progress/00000000-0000-0000-0000-000000000000" -Method Get -Headers $script:headers
}

# ===== 9. 模型版本 =====
Write-Section "[9/10] 模型版本管理"
Test-Step "列出模型版本" {
    Invoke-RestMethod "$BaseUrl/api/models/" -Method Get -Headers $script:headers
}

# ===== 10. 导出 =====
Write-Section "[10/10] 标注导出"
Test-Step "导出 COCO" {
    $curlArgs = @("-s", "-X", "GET", "$BaseUrl/api/export/coco/$script:datasetId", "-H", "Authorization: Bearer $script:token", "-w", "`nHTTP_CODE:%{http_code}")
    $r = & curl @curlArgs
    if ($r -match "HTTP_CODE:200") { return "OK" } else { throw "导出失败" }
}

Test-Step "导出 YOLO" {
    $curlArgs = @("-s", "-X", "GET", "$BaseUrl/api/export/yolo/$script:datasetId", "-H", "Authorization: Bearer $script:token", "-w", "`nHTTP_CODE:%{http_code}")
    $r = & curl @curlArgs
    if ($r -match "HTTP_CODE:200") { return "OK" } else { throw "导出失败" }
}

Test-Step "导出 CSV" {
    $curlArgs = @("-s", "-X", "GET", "$BaseUrl/api/export/csv/$script:datasetId", "-H", "Authorization: Bearer $script:token", "-w", "`nHTTP_CODE:%{http_code}")
    $r = & curl @curlArgs
    if ($r -match "HTTP_CODE:200") { return "OK" } else { throw "导出失败" }
}

# ===== 汇总 =====
Write-Section "E2E 测试结果汇总"
$total = $script:passed + $script:failed
Write-Host ""
Write-Host "  通过: $script:passed / 失败: $script:failed / 总计: $total" -ForegroundColor $(if ($script:failed -eq 0) { "Green" } else { "Red" })
Write-Host ""

if ($script:failed -gt 0) {
    Write-Host "失败步骤详情：" -ForegroundColor Red
    $script:results | Where-Object { -not $_.ok } | ForEach-Object {
        Write-Host "  - $($_.name)" -ForegroundColor Red
        Write-Host "    $($_.err)" -ForegroundColor DarkRed
    }
    exit 1
} else {
    Write-Host "  [DONE] 全部测试通过！" -ForegroundColor Green
    exit 0
}

# ===== 工具函数 =====
function New-TestPng {
    Add-Type -AssemblyName "System.Drawing"
    $bmp = New-Object System.Drawing.Bitmap 32, 32
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::FromArgb(120, 200, 80))
    $g.Dispose()
    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    return $ms.ToArray()
}

function New-TempFile {
    $f = [System.IO.Path]::GetTempFileName()
    Remove-Item $f -Force
    return New-Object System.IO.FileInfo($f + ".png")
}
