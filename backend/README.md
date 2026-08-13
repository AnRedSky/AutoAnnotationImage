# 图像标注平台 — 后端服务

> **版本**: v3.3.0
> **状态**: 稳定运行
> **技术栈**: Python 3.10+ / FastAPI / SQLAlchemy 2.0 (async) / Celery / PyTorch
> **类别**: API + 异步任务 Worker

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

本目录为图像标注平台后端服务，**统一提供 RESTful API、Celery 异步任务、SSE 实时进度**三大能力。

---

## 目录

- [一、项目概述](#一项目概述)
- [二、核心特性](#二核心特性)
- [三、技术栈](#三技术栈)
- [四、环境要求](#四环境要求)
- [五、快速开始](#五快速开始)
- [六、配置说明](#六配置说明)
- [七、项目结构](#七项目结构)
- [八、API 文档](#八api-文档)
- [九、测试](#九测试)
- [十、运维与维护](#十运维与维护)
- [十一、常见问题](#十一常见问题)

---

## 一、项目概述

### 1.1 简介

基于 FastAPI + MySQL + Redis + MinIO + Celery 的图像自动标注后端服务，统一支撑：

- **RESTful API**：60+ 端点，覆盖认证 / 数据集 / 图像 / 标注 / 训练 / 模型 / 统计 / 团队
- **Celery 异步任务**：训练任务（`train` 队列，低并发）+ 自动标注（`annotate` 队列，高并发）
- **SSE 实时进度**：训练 / 自动标注的端到端实时进度推送

### 1.2 多应用架构（v3.0 重构）

后端从单文件 `main.py` 重构为四大业务应用 + 自动发现 + 自动挂载：

| 应用 | 职责 | 主要路由前缀 |
|---|---|---|
| `admin` | 用户管理、统计、团队、系统级 | `/api/users`, `/api/stats`, `/api/teams`, `/api` |
| `auth` | 登录、注册、Token 签发 | `/api/auth` |
| `tasks` | 数据集、图像、训练、模型、检测、分割、文件、自动标注 | `/api/datasets`, `/api/images`, `/api/training`, `/api/models`, `/api/detection`, `/api/segmentation`, `/api/auto-annotate`, `/api/files` |
| `annotation` | BBox 标注、分割 mask 标注 | `/api/annotations`, `/api/detection/annotations`, `/api/segmentation/masks` |

**核心机制**：
- `AppInterface`：每个业务应用实现此接口，声明路由条目与启动钩子
- `AppRegistry.iter_routes()`：自动产出 RouteEntry，`main.py` 0 硬编码
- `MiddlewareRegistry.apply()`：中间件按 `order` 升序自动注册
- `PluginRegistry`：存储 / ML / 任务队列 / 通知四大插件可热插拔

### 1.3 子模块入口

| 入口 | 用途 | 启动命令 |
|---|---|---|
| `app.main:app` | FastAPI ASGI 应用 | `uvicorn app.main:app` |
| `app.main:run` | Console-script 入口 | `image-annotation-backend` |
| `app.tasks.workers.celery_app:run_worker` | Celery 合并 worker | `uv run worker` |
| `app.tasks.workers.celery_app:run_worker_train` | 仅 train 队列 | `uv run worker-train` |
| `app.tasks.workers.celery_app:run_worker_annotate` | 仅 annotate 队列 | `uv run worker-annotate` |
| `app.core.healthcheck:main` | 健康检查 | `uv run healthcheck` |

---

## 二、核心特性

### 2.1 异步优先

- 全栈 `async/await`，FastAPI + SQLAlchemy 2.0 async + aiomysql + aiofiles
- 长任务全部走 Celery（不阻塞 API 进程）
- 文件 I/O 全异步

### 2.2 多任务支持

| 任务 | 模型 | 训练框架 | 推理入口 |
|---|---|---|---|
| 图像分类 | timm 700+ SOTA | PyTorch + timm | `app.tasks.ml.classification` |
| 目标检测 | YOLOv8 (n/s/m/l/x) | Ultralytics | `app.tasks.ml.detection` |
| 语义分割 | DeepLabV3+ (ResNet50/101) | torchvision + albumentations | `app.tasks.ml.segmentation` |

### 2.3 实时进度推送（SSE）

- `/api/training/progress/stream/{task_id}` — 训练实时进度
- `/api/detection/progress/stream/{task_id}` — 检测训练实时进度
- 基于 `Server-Sent Events` 协议，每秒推送 1 帧

### 2.4 安全

- JWT (HS256) 强制 `iss` / `aud` 声明 + 时钟漂移容忍 (60s)
- Token 黑名单（logout 显式撤销）
- 密码 bcrypt 加密
- 生产环境 fail-fast：SECRET_KEY 弱值 / MYSQL_PASSWORD 默认值检测
- CORS 显式 origin + credentials 安全处理
- 文件端点强制鉴权（query token 兼容 `<img>` 标签）

### 2.5 可观测性

- 中间件统一注册：CORS / RequestID / RequestTiming / 错误处理
- 慢请求监控（`REQUEST_SLOW_THRESHOLD_MS`）
- 慢 SQL 监控（`SQL_SLOW_THRESHOLD_MS`）
- 业务缓存层（`CACHE_ENABLED` + Redis）

### 2.6 路径与缓存规范

- 所有磁盘路径（`UPLOAD_DIR` / `MODEL_DIR` / `PRETRAINED_CACHE_DIR` / `ULTRALYTICS_HOME`）强制锚定**项目根**，避开 cwd 依赖
- 预训练权重统一收纳到 `MODEL_DIR/cache/` 下，HF / torchvision / ultralytics 三套缓存共用
- Windows 上强制 `HF_HUB_DISABLE_SYMLINKS=1` 解决 `[WinError 14007]`

---

## 三、技术栈

| 类别 | 选型 | 版本 |
|---|---|---|
| Web 框架 | FastAPI + Uvicorn | 0.110.0 / 0.27.1 |
| 数据建模 | Pydantic + pydantic-settings | 2.6.1 / 2.2.1 |
| ORM | SQLAlchemy (async) | 2.0.27 |
| MySQL 驱动 | aiomysql + pymysql | 0.2.0 / 1.1.0 |
| 迁移 | Alembic | 1.13.1 |
| 鉴权 | python-jose + bcrypt | 3.3.0 / 4.1.2 |
| 异步任务 | Celery | 5.3.6 |
| 消息队列 / 缓存 | Redis | 5.0.3 |
| 深度学习 | PyTorch + torchvision | 2.2.0 / 0.17.0 |
| 预训练库 | timm + ultralytics | 0.9.12 / 8.0.196 |
| 图像处理 | Pillow + opencv-python + albumentations | 10.2.0 / 4.9.0.80 / 1.3.1 |
| ML 工具 | scikit-learn + numpy | 1.4.1.post1 / 1.26.4 |
| 对象存储 | minio | 7.2.4 |
| 异步文件 | aiofiles | 23.2.1 |
| 配置 | python-dotenv | 1.0.1 |
| 日志 | loguru | 0.7.2 |
| 测试 | pytest + pytest-asyncio + pytest-cov + httpx | 8.1.1 / 0.23.6 / 5.0.0 / 0.27.0 |
| 代码检查 | ruff + mypy | 0.3.5 / 1.9.0 |

包管理：[uv](https://github.com/astral-sh/uv) （`uv.lock` 是单一真实来源，`requirements.txt` 是 pip 兼容备份）

---

## 四、环境要求

### 4.1 运行时

| 软件 | 最低版本 | 用途 |
|---|---|---|
| Python | 3.10+ | 后端运行时 |
| MySQL | 8.0+ | 主数据库 |
| Redis | 7+ | 缓存 + Celery broker + Celery backend |
| MinIO | 最新稳定版 | 对象存储（可选，本地存储可替代） |
| uv | 0.9+ | Python 包管理（推荐） |

### 4.2 可选依赖

| 依赖 | 用途 | 备注 |
|---|---|---|
| NVIDIA CUDA | GPU 推理 / 训练 | 推荐 11.8+ |
| HuggingFace 镜像（国内必配） | 预训练权重下载 | `HF_ENDPOINT=https://hf-mirror.com` |

---

## 五、快速开始

### 5.1 安装依赖

#### 方式 A：使用 uv（推荐，比 pip 快 10 倍）

```bash
# 安装 uv
pip install uv

# 同步依赖（含 dev 依赖）
uv sync

# 仅运行时依赖
uv sync --no-dev
```

#### 方式 B：使用 pip

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### 5.2 配置环境变量

```bash
cp .env.example .env
# 编辑 .env: 至少改 SECRET_KEY / MYSQL_PASSWORD
```

### 5.3 启动开发服务

#### 选项 1：仅启动 API（最简）

```bash
# 前台 + 热重载
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 或后台启动 (写 PID 文件)
python start_api.py start
python start_api.py --status
python start_api.py stop

# 前台 + 热重载 (推荐开发)
python start_api.py --reload
```

#### 选项 2：API + 双队列 Worker

```bash
# 终端 1: API
uv run uvicorn app.main:app --reload --port 8000

# 终端 2: 合并 worker (train + annotate)
uv run worker
# 等价: celery -A app.tasks.workers.celery_app worker --loglevel=info

# 终端 2 备选: 拆分 worker (推荐生产)
# train:  CPU/GPU 密集, concurrency=1
uv run worker-train

# annotate:  I/O 密集, concurrency=4
uv run worker-annotate
```

> ⚠️ **Windows 必读**：`CELERY_WORKER_POOL=threads`（默认）。Linux 可改 `prefork` 获得多进程。

### 5.4 验证启动

```bash
# API 健康检查
curl http://localhost:8000/api/health
# 期望: {"status": "ok", "db": "ok", "redis": "ok", "minio": "ok"}

# API 根路径
curl http://localhost:8000/
# 期望: {"name": "Image Annotation System", "version": "1.0.0", "status": "running", "docs": "/docs"}

# OpenAPI 文档
# 浏览器访问 http://localhost:8000/docs
```

### 5.5 Docker 部署

```bash
# 单独构建
docker build -f Dockerfile.api -t annotation/api:local .
docker build -f Dockerfile.worker -t annotation/worker:local .

# 一键启动（推荐，docker-compose 编排 MySQL/Redis/MinIO/API/Worker/前端）
cd ..
docker compose up -d
```

---

## 六、配置说明

完整字段说明见 [使用手册 § 3.1](../docs/使用手册.md#三系统配置)。

### 6.1 必填项（生产环境强制）

```bash
APP_ENV=production
SECRET_KEY=<随机字符串, 至少 32 字符>
MYSQL_PASSWORD=<强密码>
```

### 6.2 数据库

```bash
# 方式 1: 完整连接串（优先）
DATABASE_URL=mysql+aiomysql://user:password@host:3306/dbname

# 方式 2: 分散配置
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=root123
MYSQL_DATABASE=image_annotation

# 连接池
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
DB_POOL_RECYCLE=3600
```

### 6.3 Redis & Celery

```bash
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

CELERY_BROKER_URL=           # 留空用 REDIS_URL
CELERY_RESULT_BACKEND=       # 留空用 REDIS_URL

CELERY_WORKER_POOL=threads   # threads (Windows) | prefork (Linux)
CELERY_WORKER_CONCURRENCY=2
```

### 6.4 存储

```bash
STORAGE_BACKEND=local         # local | minio

# 本地存储路径（默认基于项目根，相对路径会基于项目根解析）
UPLOAD_DIR=./uploads
MODEL_DIR=./models

# MinIO 配置（STORAGE_BACKEND=minio 时生效）
MINIO_ENDPOINT=127.0.0.1:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=image-annotation
MINIO_SECURE=false
```

### 6.5 ML 推理

```bash
INFERENCE_DEVICE=cpu          # cpu | cuda
DEFAULT_MODEL=efficientnet_b0
DEFAULT_CONFIDENCE_THRESHOLD=0.6
DATALOADER_WORKERS=0          # 0=同步, >0=多进程
SEG_INFERENCE_BATCH_SIZE=4
```

### 6.6 HuggingFace 镜像（国内网络必配）

```bash
HF_ENDPOINT=https://hf-mirror.com
HUGGINGFACE_HUB_ENDPOINT=https://hf-mirror.com
HF_HOME=./models/cache/huggingface

# Windows 必备
HF_HUB_DISABLE_SYMLINKS=1
HF_HUB_DISABLE_SYMLINKS_WARNING=1
HF_HUB_DOWNLOAD_TIMEOUT=60
```

### 6.7 安全

```bash
JWT_ALGORITHM=HS256
JWT_ISSUER=image-annotation
JWT_AUDIENCE=image-annotation-api
JWT_LEEWAY_SECONDS=60
ACCESS_TOKEN_EXPIRE_MINUTES=1440   # 24 小时

CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
CORS_ALLOW_CREDENTIALS=true
```

### 6.8 缓存与性能

```bash
CACHE_ENABLED=true
CACHE_DEFAULT_TTL=300
CACHE_KEY_PREFIX=app:
SQL_SLOW_THRESHOLD_MS=200
REQUEST_SLOW_THRESHOLD_MS=500
MAX_UPLOAD_SIZE_MB=20
```

---

## 七、项目结构

```
backend/
├── app/
│   ├── admin/                  # 业务应用 1/4: 用户/统计/团队/系统
│   │   ├── api/                # 路由层
│   │   │   ├── user.py
│   │   │   ├── stats.py
│   │   │   ├── system.py
│   │   │   ├── team.py
│   │   │   ├── tenant.py
│   │   │   └── __init__.py     # 路由聚合
│   │   ├── model/              # ORM (User)
│   │   ├── schema/             # Pydantic DTO
│   │   ├── service/            # 业务编排
│   │   ├── repository/         # 复杂查询
│   │   └── __init__.py         # AdminApp 注册
│   │
│   ├── auth/                   # 业务应用 2/4: 认证
│   │   ├── api/
│   │   ├── service/
│   │   ├── model/
│   │   └── __init__.py
│   │
│   ├── tasks/                  # 业务应用 3/4: 数据集/图像/训练/模型
│   │   ├── api/
│   │   │   ├── dataset.py
│   │   │   ├── files.py
│   │   │   ├── auto_annotate.py
│   │   │   ├── detection/      # 目标检测子模块
│   │   │   ├── segmentation/   # 语义分割子模块
│   │   │   ├── training/       # 训练子模块
│   │   │   ├── model/          # 模型版本子模块
│   │   │   ├── export/         # 导出子模块
│   │   │   ├── image/          # 图像子模块
│   │   │   └── preview/        # 预览子模块
│   │   ├── ml/                 # 机器学习 (分类/检测/分割推理)
│   │   │   ├── classification.py
│   │   │   ├── detection/
│   │   │   ├── segmentation/
│   │   │   ├── imagenet_common_labels.py
│   │   │   ├── ultralytics_setup.py
│   │   │   └── device_info.py
│   │   ├── model/              # ORM (Dataset, Image, Category, TrainingJob, ModelVersion, Team, TeamMember, AnnotationLog, AuditLog)
│   │   ├── repository/         # 复杂查询
│   │   ├── schema/             # Pydantic DTO
│   │   ├── service/            # 业务编排
│   │   ├── workers/            # Celery 异步任务
│   │   │   ├── celery_app.py
│   │   │   ├── classification.py
│   │   │   ├── detection/
│   │   │   ├── segmentation/
│   │   │   └── signal_handler.py
│   │   └── __init__.py
│   │
│   ├── annotation/             # 业务应用 4/4: BBox / mask 标注
│   │   ├── api/annotation.py
│   │   ├── model/
│   │   │   ├── bbox_annotation.py
│   │   │   └── segmentation_mask.py
│   │   ├── repository/
│   │   ├── schema/
│   │   ├── service/
│   │   └── __init__.py
│   │
│   ├── common/                 # 公共基础 (跨应用)
│   │   ├── interfaces.py       # AppInterface / MiddlewareInterface
│   │   ├── enums.py
│   │   ├── geometry/           # bbox/掩码几何工具
│   │   └── ...
│   │
│   ├── core/                   # 核心基础
│   │   ├── config.py           # pydantic-settings 配置
│   │   ├── security.py         # JWT 签发/验证
│   │   ├── deps.py             # FastAPI Depends
│   │   ├── cli.py              # CLI 入口
│   │   ├── healthcheck.py      # 健康检查 CLI
│   │   └── startup_profiler.py # 启动耗时分析
│   │
│   ├── database/               # 数据层
│   │   ├── __init__.py         # engine, init_db, get_db
│   │   ├── redis.py            # redis_client
│   │   └── minio_client.py     # MinIO 客户端
│   │
│   ├── middleware/             # 中间件
│   │   ├── http/
│   │   │   ├── cors.py
│   │   │   ├── error_handler.py
│   │   │   ├── request_id.py
│   │   │   ├── request_timing.py
│   │   │   └── auth.py
│   │   └── __init__.py
│   │
│   ├── schemas/                # 跨应用 Pydantic DTO
│   ├── plugin/                 # 插件 (存储/ML/任务队列/通知)
│   ├── registry.py             # App/Middleware/Plugin Registry
│   ├── main.py                 # FastAPI 入口
│   ├── __main__.py             # python -m app
│   └── __init__.py
│
├── tests/                      # pytest 套件 (43 文件)
│   ├── conftest.py
│   ├── test_*.py               # 单元测试
│   ├── e2e_*.py                # 端到端测试
│   └── fixtures/
│
├── docs/                       # 后端历史报告
│   ├── 38-后端架构评估与拆分部署方案.md
│   ├── 40-健康端到端验证.md
│   ├── 41-CeleryWorker端到端验证.md
│   └── ... (历史变更记录)
│
├── scripts/                    # 辅助脚本
├── migrations/                 # Alembic 迁移
├── models/                     # 训练产物 (gitignore)
├── uploads/                    # 上传文件 (gitignore)
│
├── pyproject.toml              # uv 单一真实来源
├── uv.lock                     # 依赖锁定
├── requirements.txt            # pip 兼容备份
├── Dockerfile                  # 通用镜像
├── Dockerfile.api              # API 镜像
├── Dockerfile.worker           # Worker 镜像
├── start_api.py                # API 启动入口
├── start_workers.py            # Worker 启动入口
└── README.md                   # ← 本文件
```

---

## 八、API 文档

完整 OpenAPI 文档：启动后访问 http://localhost:8000/docs

### 8.1 认证

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 登录（form 提交） |
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/logout` | 登出（撤销 Token） |
| GET | `/api/auth/me` | 获取当前用户 |

### 8.2 数据集

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/datasets` | 列表 |
| POST | `/api/datasets` | 创建 |
| GET | `/api/datasets/{id}` | 详情 |
| DELETE | `/api/datasets/{id}` | 删除 |
| GET | `/api/datasets/{id}/categories` | 类别列表 |
| POST | `/api/datasets/{id}/categories` | 添加类别 |

### 8.3 图像

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/images/upload/{dataset_id}` | 批量上传 |
| GET | `/api/images/list/{dataset_id}` | 列表（支持状态过滤） |
| GET | `/api/images/{id}` | 详情 |
| DELETE | `/api/images/{id}` | 删除 |
| POST | `/api/images/auto-label/{dataset_id}` | AI 预标注 |
| POST | `/api/images/preview-confidence` | 非破坏性预览置信度 |

### 8.4 标注

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/annotations/save` | 分类标注保存 |
| POST | `/api/annotations/clear` | 批量清除标注 |
| POST | `/api/annotations/mark-unqualified` | 标记不合格 |
| POST | `/api/annotations/unmark-unqualified` | 取消不合格标记 |
| POST | `/api/annotations/batch-mark-unqualified` | 批量标记不合格 |
| GET | `/api/annotations/stats/{dataset_id}` | 标注统计 |
| GET | `/api/annotations/list/{dataset_id}` | 标注日志 |
| GET | `/api/annotations/recent` | 最近标注 |

### 8.5 目标检测

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/detection/annotations/save` | BBox 保存 |
| POST | `/api/detection/annotations/replace` | 单图 BBox 全量替换 |
| GET | `/api/detection/annotations/{image_id}` | 拉取 BBox |
| DELETE | `/api/detection/annotations/clear/{image_id}` | 清空 BBox |
| DELETE | `/api/detection/annotations/{bbox_id}` | 删除单条 |
| POST | `/api/detection/annotations/batch` | 批量入库 |
| GET | `/api/detection/copy-suggestion/{image_id}` | 跨图复制建议 |
| POST | `/api/detection/train` | YOLO 训练 |
| POST | `/api/detection/auto-annotate` | 自训练 YOLO 自动标注 |
| POST | `/api/detection/auto-annotate-pretrained` | 预训练 YOLO 自动标注 |
| GET | `/api/detection/progress/{task_id}` | 进度（旧轮询） |
| GET | `/api/detection/progress/stream/{task_id}` | SSE 实时进度 |
| GET | `/api/detection/history/{task_id}` | 训练历史曲线 |

### 8.6 语义分割

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/segmentation/masks/upload/{image_id}` | mask 上传 |
| GET | `/api/segmentation/masks/{image_id}` | 拉取 mask |
| DELETE | `/api/segmentation/masks/{image_id}` | 删除 mask |
| POST | `/api/segmentation/train` | 分割训练 |
| POST | `/api/auto-annotate/run-segmentation-pretrained` | 预训练 DeepLabV3+ 推理 |

### 8.7 训练任务

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/training/start` | 启动训练 |
| GET | `/api/training/jobs` | 任务列表（分页+过滤） |
| GET | `/api/training/jobs/{id}` | 任务详情 |
| POST | `/api/training/jobs/{id}/start` | 重跑/恢复 |
| POST | `/api/training/jobs/{id}/pause` | 暂停 |
| POST | `/api/training/jobs/{id}/cancel` | 取消 |
| POST | `/api/training/jobs/{id}/error` | 错误详情 |
| PATCH | `/api/training/jobs/{id}` | 编辑参数 |
| DELETE | `/api/training/jobs/{id}` | 删除 |
| GET | `/api/training/progress/{task_id}` | 旧轮询进度 |
| GET | `/api/training/progress/stream/{task_id}` | **SSE 实时进度** |
| GET | `/api/training/history/{task_id}` | 训练历史曲线 |
| GET | `/api/training/jobs/{id}/log` | 训练日志 |
| POST | `/api/training/jobs/{id}/log` | 追加日志 |

### 8.8 模型版本

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/models/` | 模型列表 |
| GET | `/api/models/active` | 激活模型 |
| GET | `/api/models/{id}/detail` | 模型详情 |
| POST | `/api/models/{id}/activate` | 激活 |
| POST | `/api/models/{id}/deactivate` | 取消激活 |
| DELETE | `/api/models/{id}` | 删除 |
| POST | `/api/models/batch-activate` | 批量激活 |
| POST | `/api/models/batch-delete` | 批量删除 |

### 8.9 导出

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/export/coco/{dataset_id}` | 导出 COCO |
| GET | `/api/export/yolo/{dataset_id}` | 导出 YOLO |
| GET | `/api/export/csv/{dataset_id}` | 导出 CSV |

### 8.10 统计

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/stats/overview` | 全局概览 |
| GET | `/api/stats/dataset/{id}` | 数据集维度 |
| GET | `/api/stats/confidence/{id}` | 置信度分布 |
| GET | `/api/stats/timeline/{id}` | 时间线 |
| GET | `/api/stats/annotator-efficiency` | 标注员效率 |

### 8.11 团队

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/teams` | 创建团队 |
| GET | `/api/teams` | 团队列表 |
| GET | `/api/teams/{id}` | 详情 |
| PATCH | `/api/teams/{id}` | 编辑 |
| DELETE | `/api/teams/{id}` | 删除 |
| POST | `/api/teams/{id}/members` | 邀请成员 |
| DELETE | `/api/teams/{id}/members/{user_id}` | 移除成员 |

### 8.12 用户管理（admin）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/users` | 用户列表 |
| POST | `/api/users` | 创建用户 |
| PATCH | `/api/users/{id}/role` | 改角色 |
| POST | `/api/users/{id}/reset-password` | 重置密码 |
| POST | `/api/users/{id}/activate` | 激活 |
| POST | `/api/users/{id}/deactivate` | 停用 |
| DELETE | `/api/users/{id}` | 删除用户 |

### 8.13 系统 & 文件

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查（DB/Redis/MinIO） |
| GET | `/api/system/info` | 系统信息 |
| GET | `/api/files/{id}` | 原图（query token） |
| GET | `/api/files/{id}/thumbnail?size=` | 缩略图（query token） |
| GET | `/api/auto-annotate/run` | AI 自动标注入口（同步/异步） |
| GET | `/api/auto-annotate/status/{task_id}` | 自动标注状态 |
| GET | `/api/auto-annotate/models` | 可用模型列表 |

---

## 九、测试

### 9.1 测试覆盖

后端包含 **43 个 pytest 测试文件**，覆盖：

| 测试模块 | 覆盖范围 |
|---|---|
| 认证 (`test_auth_*`) | 登录、注册、Token 签发、撤销、JWT 验证 |
| 数据集 (`test_datasets.py`) | CRUD、权限、批量操作 |
| 图像 (`test_images.py`, `test_image_endpoints.py`) | 上传、列表、缩略图、删除 |
| 标注 (`test_annotations.py`) | 分类标注保存、清空、不合格标记 |
| 目标检测 (`test_detection_*`) | BBox CRUD、YOLO 训练、预标注自动标注、训练历史 |
| 图像分割 (`test_segmentation_*`) | mask CRUD、DeepLabV3+ 训练、分割导出 |
| 训练任务 (`test_training.py`, `test_*_train.py`) | 启动、暂停、取消、编辑、删除、错误查询 |
| 模型服务 (`test_model_service_no_n_plus_1.py`) | N+1 查询优化、激活/取消激活 |
| 健康检查 (`test_health_endpoint.py`) | 系统级、API 级、worker 级 |
| 存储 (`test_storage_service.py`, `test_thumbnail_cache.py`) | MinIO / 本地存储、缩略图缓存 |
| 中间件 (`test_auth_middleware.py`) | CORS、RequestID、错误处理 |
| 迁移 (`test_migrate_v2_0_0.py`) | 数据迁移兼容性 |
| E2E (`test_e2e_v2_sprint8.py`, `e2e_*`) | 端到端完整业务流程 |

### 9.2 运行测试

```bash
# 全部测试
uv run pytest

# 详细输出
uv run pytest -v

# 覆盖率报告
uv run pytest --cov=app --cov-report=html
# 浏览器打开 htmlcov/index.html

# 指定文件
uv run pytest tests/test_auth.py -v

# 指定 marker
uv run pytest -m "not slow"          # 跳过慢测试
uv run pytest -m "not gpu"            # 跳过 GPU 测试
uv run pytest -m "eager"              # Celery eager 模式 worker 测试
```

### 9.3 测试配置

`pyproject.toml` pytest 配置：

```toml
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "slow: 慢测试 (大模型/大数据集, 默认跳过)",
    "gpu: 需要 CUDA 设备",
    "eager: Celery eager 模式 worker 测试",
]
addopts = [
    "-ra",
    "--strict-markers",
    "--strict-config",
    "--cov=app",
    "--cov-report=term-missing:skip-covered",
    "--cov-fail-under=39",
]
```

### 9.4 端到端测试

```bash
# 完整业务流程
cd ../scripts
python run_e2e.py
# 或
pwsh e2e_test.ps1
```

---

## 十、运维与维护

### 10.1 启动 / 停止服务

#### API 服务

```bash
# 后台启动 (写 PID 文件)
python start_api.py start

# 前台 + 热重载 (开发)
python start_api.py --reload

# 状态 / 停止
python start_api.py --status
python start_api.py stop
```

#### Worker 服务

```bash
# 启动双队列 worker
python start_workers.py start

# 状态 / 停止
python start_workers.py --status
python start_workers.py stop
```

### 10.2 数据库迁移

迁移脚本位于 `backend/migrations/`, 采用**两位数字编号 + 严格升序执行**的规范化体系:

```bash
# 列出所有迁移
python -m migrations.runner --list

# 校验 _registry 与磁盘文件一致性
python -m migrations.runner --check

# 执行全部迁移 (应用启动时自动调用)
python -m migrations.runner

# 只跑某一条
python -m migrations.runner --only 03

# 从某条开始 (含) 一直跑到末尾
python -m migrations.runner --from 05
```

**编号规则**:
- 文件名格式: `NN_name.py` (NN 为两位数字, 升序连续, 允许预留空号)
- 严格按 `_registry.MIGRATIONS` 列表的 `order` 字段升序执行
- 后一个脚本不得依赖前一个脚本未生成的列/表/索引
- 全部幂等 (基于 `information_schema` 判定)
- 失败立即停止, 防止数据库结构异常

**新增迁移的标准流程** (详见 `migrations/README.md`):
1. 取当前最大序号 + 1
2. 创建 `NN_descriptive_name.py`, 暴露 `async def run_migration()`
3. 在 `_registry.MIGRATIONS` 追加 `Migration(order, name, description, module)`
4. 跑 `--check` 校验一致性
5. 加 smoke test

历史 Alembic 工作流保留 (但本项目当前以 migrations/ 顺序脚本为主):

```bash
# 生成迁移 (Alembic 兼容)
alembic revision --autogenerate -m "描述变更"

# 应用迁移
alembic upgrade head

# 回滚
alembic downgrade -1
```

### 10.3 数据备份与恢复

```bash
# MySQL 备份
docker exec annotation_mysql mysqldump -uroot -proot123 image_annotation > backup_$(date +%Y%m%d).sql

# MySQL 恢复
cat backup_20260801.sql | docker exec -i annotation_mysql mysql -uroot -proot123 image_annotation

# 文件备份
tar -czf uploads_$(date +%Y%m%d).tar.gz uploads/
tar -czf models_$(date +%Y%m%d).tar.gz models/   # 训练产物（可能很大）
```

### 10.4 健康检查

```bash
# 完整健康（DB/Redis/MinIO）
curl http://localhost:8000/api/health
# {"status": "ok", "db": "ok", "redis": "ok", "minio": "ok"}

# 仅 API 进程存活
curl http://localhost:8000/

# CLI 健康检查（不依赖 HTTP）
uv run healthcheck
```

### 10.5 性能调优

```bash
# 数据库连接池
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=40
DB_POOL_RECYCLE=3600

# Celery 并发
CELERY_WORKER_CONCURRENCY=1     # CPU/GPU 训练
# worker-annotate 在 docker-compose 中 --concurrency=4 (I/O 密集)

# 缓存
CACHE_ENABLED=true
CACHE_DEFAULT_TTL=300
```

### 10.6 升级

```bash
cd backend
git pull
uv sync                         # 更新依赖
alembic upgrade head            # 应用迁移
docker compose restart api worker-train worker-annotate
```

### 10.7 代码质量

```bash
# 格式化
uv run ruff format

# 检查
uv run ruff check

# 类型检查
uv run mypy app/

# 全部走一遍
uv run ruff format && uv run ruff check && uv run mypy app/ && uv run pytest
```

`pyproject.toml` ruff 配置：

```toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]
ignore = ["E501"]
```

### 10.8 监控与日志

| 组件 | 日志位置 |
|---|---|
| API | `logs/api.log` |
| Worker | `logs/worker_*.log` |
| Docker | `docker compose logs <service>` |

慢请求监控：`REQUEST_SLOW_THRESHOLD_MS=500`（默认），超过则 WARNING 日志。
慢 SQL 监控：`SQL_SLOW_THRESHOLD_MS=200`（默认）。

---

## 十一、常见问题

### 11.1 安装与启动

**Q: `uv sync` 失败？**
A: 升级 uv 到最新版 `pip install -U uv`，或改用 `pip install -r requirements.txt`。

**Q: Docker 启动后 API 一直重启？**
A: 查看 `docker compose logs api`，通常为数据库未就绪或 .env 配错。

**Q: Windows 上 Celery 报 `ValueError: not enough values to unpack`？**
A: Windows 不支持 prefork，确保 `CELERY_WORKER_POOL=threads`。

**Q: HF 下载失败 / WinError 14007？**
A: 确认 `HF_ENDPOINT=https://hf-mirror.com` 与 `HF_HUB_DISABLE_SYMLINKS=1` 已配置。

**Q: SECRET_KEY 怎么生成？**
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 11.2 数据库

**Q: 启动报 "Can't connect to MySQL"？**
A: 检查 MySQL 是否启动、`MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_PASSWORD` 是否正确。

**Q: Alembic 迁移冲突？**
A: 多人协作时 `alembic merge` 合并分支，或 `alembic stamp head` 重置基线。

**Q: 数据库连接耗尽？**
A: 调高 `DB_POOL_SIZE=20` / `DB_MAX_OVERFLOW=40`，并检查是否有未关闭的连接。

### 11.3 训练 / Worker

**Q: 训练任务一直 PENDING 不动？**
A: 检查 `worker-train` 容器是否运行，`docker compose ps`，查看 `docker compose logs worker-train`。

**Q: 训练报错 "No module named 'ultralytics'"？**
A: `uv sync` 或 `pip install ultralytics==8.0.196`。

**Q: 训练 OOM？**
A: 减小 `batch_size` / `imgsz`，检查 GPU 显存，或改用 CPU 训练。

**Q: Celery worker 不消费任务？**
A: 检查 Redis 连通性：`redis-cli -h $REDIS_HOST ping`，检查 Celery 配置 `CELERY_BROKER_URL`。

### 11.4 安全

**Q: 生产环境默认密码还能登录吗？**
A: `APP_ENV=production` 启动时会检查默认值，发现默认值直接拒绝启动。务必修改。

**Q: 怎么撤销已签发的 Token？**
A: 调用 `POST /api/auth/logout` 写黑名单，或修改 `SECRET_KEY` 强制全部失效。

**Q: 文件端点 401？**
A: `<img>` 标签无法设 header，前端已自动拼 `?token=xxx` query 参数；如手动调用，URL 必须带 token。

### 11.5 性能

**Q: API 响应慢？**
A: 检查慢请求日志（`REQUEST_SLOW_THRESHOLD_MS`），优化对应 SQL；启用缓存 (`CACHE_ENABLED=true`)。

**Q: SSE 频繁断连？**
A: 检查反向代理（nginx）的 `proxy_buffering off` 与超时设置。

**Q: 图片列表加载慢？**
A: 加分页参数，缩略图已 Redis 缓存。

---

## 十二、附录

### 12.1 关联文档

- [项目 README](../README.md) — 项目总览
- [项目说明文档](../docs/项目说明文档.md) — 项目实现价值
- [使用手册](../docs/使用手册.md) — 完整使用指南
- [代码优化迭代记录](../docs/代码优化迭代记录.md) — 性能优化历史

### 12.2 包管理规范

- **依赖列表**：以 `pyproject.toml` 为**单一真实来源**
- **依赖锁定**：`uv.lock` 必须随代码提交
- **`requirements.txt`**：由 `uv export` 同步生成，**禁止手动编辑**
- **Docker 构建**：使用 `uv sync --frozen --no-install-project`，确保 lock 一致
- **新依赖**：`uv add <pkg>`（自动写 `pyproject.toml` + 更新 `uv.lock`）

### 12.3 console_scripts 入口

`pyproject.toml` 中声明的 CLI：

```toml
[project.scripts]
api              = "app.main:run"
worker           = "app.tasks.workers.celery_app:run_worker"
worker-train     = "app.tasks.workers.celery_app:run_worker_train"
worker-annotate  = "app.tasks.workers.celery_app:run_worker_annotate"
healthcheck      = "app.core.healthcheck:main"
```

安装后可全局调用：`image-annotation-backend` / `worker` / `worker-train` / `worker-annotate` / `healthcheck`

## License

MIT
