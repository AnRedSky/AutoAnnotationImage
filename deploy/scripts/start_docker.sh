#!/usr/bin/env bash
# ============================================================
#  一键部署脚本 - Linux / macOS
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
#    ./scripts/start_docker.sh                  # 默认启动
#    ./scripts/start_docker.sh --rebuild         # 强制重新构建镜像
#    ./scripts/start_docker.sh --skip-build     # 跳过构建
#    ./scripts/start_docker.sh --status         # 仅查看状态
#    ./scripts/start_docker.sh --stop           # 停止所有服务
#    ./scripts/start_docker.sh --logs           # 查看日志
#    ./scripts/start_docker.sh --verify         # 仅运行验证
#    ./scripts/start_docker.sh --reset          # 停止并删除卷
# ============================================================
set -e

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
ENV_EXAMPLE="$ROOT_DIR/.env.example"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"

# 参数
REBUILD=false
SKIP_BUILD=false
STATUS_ONLY=false
STOP_ONLY=false
SHOW_LOGS=false
VERIFY_ONLY=false
RESET=false
for arg in "$@"; do
    case $arg in
        --rebuild|-r)    REBUILD=true ;;
        --skip-build)    SKIP_BUILD=true ;;
        --status)        STATUS_ONLY=true ;;
        --stop)          STOP_ONLY=true ;;
        --logs|-l)       SHOW_LOGS=true ;;
        --verify)        VERIFY_ONLY=true ;;
        --reset)         RESET=true ;;
        --help|-h)
            head -n 25 "$0" | tail -n 23
            exit 0
            ;;
    esac
done

# 工具函数
step() { echo -e "\n${CYAN}▶ $1${NC}"; }
ok() { echo -e "  ${GREEN}✓${NC} $1"; }
info() { echo -e "  ${YELLOW}ℹ${NC} $1"; }
err() { echo -e "  ${RED}✗${NC} $1"; }
header() {
    echo -e "\n${MAGENTA}============================================================${NC}"
    echo -e "${MAGENTA}  $1${NC}"
    echo -e "${MAGENTA}============================================================${NC}"
}

header "图像自动标注系统 - 一键部署"
info "项目根: $ROOT_DIR"
info "Compose: $COMPOSE_FILE"
info "Env:     $ENV_FILE"

# ============================================================
# 步骤 1: 准备 .env
# ============================================================
step "1) 准备 .env 文件"
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        info ".env 不存在, 从 .env.example 复制"
        cp "$ENV_EXAMPLE" "$ENV_FILE"
    else
        err ".env 和 .env.example 都不存在, 无法继续"
        exit 1
    fi
    info "正在生成强随机密钥..."
    python3 "$SCRIPT_DIR/gen_secrets.py" --write --env-file "$ENV_FILE"
    ok "已生成 .env (含强随机密钥)"
else
    ok ".env 已存在"
    if grep -q "CHANGEME" "$ENV_FILE"; then
        info "检测到 CHANGEME 占位符, 正在替换..."
        python3 "$SCRIPT_DIR/gen_secrets.py" --write --env-file "$ENV_FILE"
        ok "已替换占位符"
    fi
fi

# ============================================================
# 步骤 2: 检查 Docker
# ============================================================
step "2) 检查 Docker 环境"
if ! command -v docker &> /dev/null; then
    err "未找到 docker, 请先安装 Docker"
    exit 1
fi
ok "$(docker --version)"

if ! docker compose version &> /dev/null; then
    err "未找到 docker compose, 请升级 Docker"
    exit 1
fi
ok "$(docker compose version)"

# ============================================================
# 分支: 仅查看状态
# ============================================================
if [ "$STATUS_ONLY" = true ]; then
    step "服务状态"
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps
    info "URLs:"
    info "  前端:    http://localhost:8080"
    info "  API:     http://localhost:8000"
    info "  Swagger: http://localhost:8000/docs"
    info "  MinIO:   http://localhost:9001 (Console)"
    exit 0
fi

# ============================================================
# 分支: 停止服务
# ============================================================
if [ "$STOP_ONLY" = true ]; then
    step "停止所有服务"
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down
    ok "已停止"
    exit 0
fi

# ============================================================
# 分支: 查看日志
# ============================================================
if [ "$SHOW_LOGS" = true ]; then
    step "查看日志 (Ctrl+C 退出)"
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" logs -f --tail=100
    exit 0
fi

# ============================================================
# 分支: 重置
# ============================================================
if [ "$RESET" = true ]; then
    step "重置 (停止 + 删除卷)"
    read -p "  确认要删除所有数据? (yes/no): " conf
    if [ "$conf" = "yes" ]; then
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down -v
        ok "已删除所有卷"
    else
        info "已取消"
    fi
    exit 0
fi

# ============================================================
# 分支: 仅验证
# ============================================================
if [ "$VERIFY_ONLY" = true ]; then
    step "运行部署验证"
    python3 "$SCRIPT_DIR/verify_deployment.py" --skip-build --env-file "$ENV_FILE"
    exit $?
fi

# ============================================================
# 步骤 3: 构建镜像
# ============================================================
if [ "$SKIP_BUILD" = false ]; then
    step "3) 构建 Docker 镜像"
    if [ "$REBUILD" = true ]; then
        info "强制重建 (--no-cache)..."
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build --no-cache
    else
        info "增量构建..."
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build
    fi
    ok "镜像构建完成"
else
    step "3) 跳过构建 (--skip-build)"
fi

# ============================================================
# 步骤 4: 启动所有服务
# ============================================================
step "4) 启动所有服务 (docker compose up -d)"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
ok "所有服务已启动"

# ============================================================
# 步骤 5: 等待服务 healthy
# ============================================================
step "5) 等待服务 healthy (最多 90s)"
MAX_WAIT=90
START_TIME=$(date +%s)
SERVICES=("mysql" "redis" "minio" "api" "frontend")
ALL_HEALTHY=false
while [ $(($(date +%s) - START_TIME)) -lt $MAX_WAIT ]; do
    UNHEALTHY=()
    for svc in "${SERVICES[@]}"; do
        STATE=$(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps --format json 2>/dev/null | python3 -c "import json,sys
try:
    for line in sys.stdin:
        d = json.loads(line)
        if d.get('Service') == '$svc':
            print(d.get('State', 'unknown'))
            sys.exit(0)
    print('missing')
except: print('error')")
        if [ "$STATE" != "running" ]; then
            UNHEALTHY+=("$svc=$STATE")
        fi
    done
    if [ ${#UNHEALTHY[@]} -eq 0 ]; then
        ALL_HEALTHY=true
        break
    fi
    info "等待: ${UNHEALTHY[*]}"
    sleep 5
done
if [ "$ALL_HEALTHY" = true ]; then
    ok "所有核心服务 healthy"
else
    info "部分服务未 healthy, 但启动流程已结束"
    info "查看状态: docker compose ps"
fi

# ============================================================
# 步骤 6: 自动 bootstrap admin (仅首启 + .env 配置)
# ============================================================
# 触发条件 (同时满足):
#   1) .env 中 ADMIN_USERNAME 与 ADMIN_PASSWORD 均非空
#   2) 当前 API 容器可达 (healthy)
# 行为: 在 API 容器内执行 `python -m scripts.bootstrap_admin --check`
#       → 若返回 "无 admin", 自动执行不带 --check 的 bootstrap (非交互)
#       → 若返回 "已有 admin", 静默跳过
# 失败仅 WARN, 不阻塞后续 verify
step "6) 自动 bootstrap admin (仅首启)"
if grep -qE "^ADMIN_USERNAME=.+$" "$ENV_FILE" && grep -qE "^ADMIN_PASSWORD=.+$" "$ENV_FILE"; then
    CHECK_OUT=$(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T api \
        python -m scripts.bootstrap_admin --check 2>&1 || true)
    echo "$CHECK_OUT" | sed 's/^/      /'
    if echo "$CHECK_OUT" | grep -q "系统无任何 admin 用户"; then
        info "系统无 admin, 按 .env 自动创建..."
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T api \
            python -m scripts.bootstrap_admin || warn "自动 bootstrap 失败, 可手动: docker compose exec api python -m scripts.bootstrap_admin"
    else
        ok "系统已有 admin, 跳过自动 bootstrap"
    fi
else
    info ".env 未配置 ADMIN_USERNAME/ADMIN_PASSWORD, 跳过 (如需首启初始化, 编辑 .env 后重跑)"
fi

# ============================================================
# 步骤 7: 部署后验证
# ============================================================
step "7) 部署后验证"
info "等待 API 启动 (15s)..."
sleep 15
python3 "$SCRIPT_DIR/verify_deployment.py" --skip-build --env-file "$ENV_FILE" || true

# ============================================================
# 总结
# ============================================================
header "部署完成"
echo -e "  ${GREEN}前端:${NC}    http://localhost:8080"
echo -e "  ${GREEN}API:${NC}     http://localhost:8000"
echo -e "  ${GREEN}Swagger:${NC} http://localhost:8000/docs"
echo -e "  ${GREEN}MinIO:${NC}   http://localhost:9001"
echo ""
info "常用命令:"
echo -e "  ${YELLOW}查看状态:${NC} docker compose ps"
echo -e "  ${YELLOW}查看日志:${NC} docker compose logs -f"
echo -e "  ${YELLOW}停止服务:${NC} docker compose down"
echo -e "  ${YELLOW}重置数据:${NC} docker compose down -v"
