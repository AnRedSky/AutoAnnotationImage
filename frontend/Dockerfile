# syntax=docker/dockerfile:1.7
# =====================================================================
#  Image Annotation Frontend - 多阶段构建 (v3.3.0)
# ---------------------------------------------------------------------
#  Stage 1 (builder): Node 20 + npm install + Vite build
#  Stage 2 (runtime): Nginx alpine 提供 dist/ 静态资源 + /api 反代
#
#  镜像大小:
#    builder stage:   ~500 MB (含 node_modules, 构建期)
#    final runtime:   ~50 MB  (nginx alpine + dist)
#
#  关键设计:
#    1) 层缓存分离: package*.json 独立层, 源码改动不破坏依赖缓存
#    2) 非 root 用户: nginx.conf 切换为非特权端口 (8080)
#    3) HEALTHCHECK: 暴露 /healthz, compose 可基于此判断存活
#    4) ARG 注入: 构建期通过 VITE_API_BASE_URL 决定生产 API baseURL
# =====================================================================

# ============================================================
#  Stage 1: builder — 编译 Vue 3 + Vite
# ============================================================
ARG NODE_VERSION=20-alpine

FROM node:${NODE_VERSION} AS builder

# pnpm / npm 选择: 该项目使用 npm (见 frontend/package-lock.json)
WORKDIR /build

# 依赖层缓存: 仅 package*.json 变化才重装
COPY package.json package-lock.json ./

# 1) 优先用 ci (lock 文件必严格一致); CI 模式下用 ci 更快
# 2) 失败时 fallback 到 install (允许 lock 与 package.json 存在小偏差)
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

# 应用代码层 (独立于依赖层)
COPY . .

# 生产构建: Vite 会自动加载 .env.production
# 构建参数允许外部覆盖 API baseURL (默认: 同源 /api)
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

# 输出到 /build/dist
RUN npm run build

# ============================================================
#  Stage 2: runtime — Nginx 托管 dist/
# ============================================================
FROM nginx:1.27-alpine AS runtime

# 替换默认 nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 复制构建产物 (排除 source maps / 临时文件)
COPY --from=builder /build/dist /usr/share/nginx/html

# 健康检查端点
RUN echo "ok" > /usr/share/nginx/html/healthz

# 非 root 运行 nginx (使用 8080 端口)
# 1) 创建非特权用户 (alpine 自带 nginx 用户, 但 UID 不固定, 显式创建)
# 2) 修改 nginx.conf 中 listen 8080
# 3) chown 必要的目录
RUN sed -i 's/listen\s*80;/listen 8080;/' /etc/nginx/conf.d/default.conf \
    && addgroup -g 10001 -S app 2>/dev/null || true \
    && adduser -u 10001 -S app -G app 2>/dev/null || true \
    && touch /var/run/nginx.pid \
    && chown -R app:app /var/cache/nginx /var/log/nginx /usr/share/nginx/html /var/run/nginx.pid /etc/nginx/conf.d \
    && chmod -R 755 /usr/share/nginx/html

USER app

EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null 2>&1 || exit 1

# 前台运行 nginx
CMD ["nginx", "-g", "daemon off;"]
