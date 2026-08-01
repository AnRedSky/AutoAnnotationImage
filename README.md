# 图像标注平台 (Image Annotation Platform)

> **版本**: v3.3.1
> **状态**: 稳定运行
> **类别**: 基于深度学习的协同标注与训练系统
> **核心范式**: AI 预标注 + 人工修正 + 增量训练
> **部署方式**: 一键 Docker Compose（统一 `.env` 配置文件）

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/Vue-3.4-brightgreen)](https://vuejs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688)](https://fastapi.tiangolo.com/)
[![MySQL](https://img.shields.io/badge/MySQL-8.0-4479A1)](https://www.mysql.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## 目录

- [一、项目概述](#一项目概述)
- [二、核心特性](#二核心特性)
- [三、技术架构](#三技术架构)
- [四、环境要求](#四环境要求)
- [五、快速开始](#五快速开始)
- [六、配置说明](#六配置说明)
- [七、核心业务流程](#七核心业务流程)
- [八、API 概览](#八api-概览)
- [九、运维与维护](#九运维与维护)
- [十、常见问题](#十常见问题)
- [十一、版本演进](#十一版本演进)
- [十二、许可](#十二许可)

---

## 一、项目概述

### 1.1 系统简介

本平台是一款面向**工业质检、内容审核、商品识别、医疗影像**等真实业务场景的**协同式**图像标注与人机协同训练系统，统一支持**图像分类、目标检测、语义分割**三类任务。

平台以"AI 预标注 + 人工修正"为核心范式，把传统的全人工标注工作流提升为 AI 辅助 + 人工复核模式：

- **AI 预标注**：调用 timm / YOLOv8 / DeepLabV3+ 模型对图片批量推理
- **人工修正**：标注员在预标注结果上**快速确认或修正**
- **增量训练**：人工修正的数据自动反哺训练集，一键 Fine-tune 出新模型
- **协同管理**：团队 / 成员 / 角色 / 数据集共享

### 1.2 业务应用场景

| 场景     | 典型价值                                 |
| -------- | ---------------------------------------- |
| 工业质检 | 替代人工目检，缺陷漏检率下降 60%+        |
| 内容审核 | 违规图片自动分流，人工仅复核低置信度样本 |
| 商品识别 | 新品上架自动归类，人工微调少量边界样本   |
| 医疗影像 | AI 预筛 + 医生复核，诊断效率显著提升     |
| 安防监控 | 异常行为自动告警，安全人员聚焦高风险事件 |

### 1.3 子模块文档

| 模块            | 文档                                        |
| --------------- | ------------------------------------------- |
| 后端            | [backend/README.md](backend/README.md)       |
| 前端            | (本文档统一说明)                            |
| 项目说明        | [docs/项目说明文档.md](docs/项目说明文档.md) |
| 使用手册        | [docs/使用手册.md](docs/使用手册.md)         |
| 部署 / 运维记录 | [docs/](docs/)                               |

---

## 二、核心特性

### 2.1 三类任务统一支持

| 任务类型 | 模型后端                  | 输出               | 标注形态     |
| -------- | ------------------------- | ------------------ | ------------ |
| 图像分类 | timm (700+ SOTA)          | 单标签             | 键盘快速确认 |
| 目标检测 | YOLOv8 (n/s/m/l/x)        | 多目标 BBox + 类别 | 鼠标拖拽     |
| 语义分割 | DeepLabV3+ (ResNet50/101) | 像素级 mask PNG    | mask 上传    |

### 2.2 AI 预标注 + 人工修正

- **Top-5 候选标签展示**，按置信度降序
- **置信度阈值分流**：≥ 阈值自动标 `ai_labeled`；< 阈值保持 `pending` 等人工
- **非破坏性预览**：跑前可预览哪些图会被自动标

### 2.3 高效人工 UI

- 键盘快捷键（数字键选 / A 接受 / D 跳过 / → 下一张）
- 实时计时（AI 节省时间可量化）
- 批量操作、跨图复制建议
- 不合格图片标记（reason + 自定义文案）

### 2.4 增量训练闭环

- 人工修正的数据自动加入训练集
- 一键启动训练（SSE 实时进度 + 训练曲线）
- 多模型版本、多激活并存

### 2.5 团队协作

- 团队 / 成员 / 角色三级管理
- 数据集共享（owner / editor / viewer）
- 跨用户邀请、重复邀请检测

### 2.6 完整审计

- 所有标注行为写日志（confirm / correct + 耗时）
- 关键管理动作全程可追溯
- 审计日志页（admin 可见）

### 2.7 多格式导出

- **COCO** — 直接用于 mmdetection / detectron2
- **YOLO** — 直接用于 ultralytics / YOLOv5
- **CSV** — 通用格式

### 2.8 实时 SSE 进度

- 训练 / 自动标注任务端到端实时进度推送
- 单例 SSE 池 + 引用计数（同一任务始终仅 1 条连接）

---

## 三、技术架构

### 3.1 整体技术栈

| 层级         | 选型                                       | 版本                          |
| ------------ | ------------------------------------------ | ----------------------------- |
| 前端框架     | Vue 3 + Vite + TypeScript                  | Vue 3.4+                      |
| 前端 UI      | Element Plus + ECharts + Pinia             | Element Plus 2.8+             |
| 前端 HTTP    | Axios                                      | 1.7+                          |
| 后端框架     | FastAPI + Uvicorn                          | 0.110+                        |
| 后端 ORM     | SQLAlchemy 2.0 (async) + Alembic           | 2.0.27+                       |
| 后端数据建模 | Pydantic v2 + pydantic-settings            | 2.6+                          |
| 数据库       | MySQL 8.0                                  | 8.0+                          |
| 缓存 / 队列  | Redis 7                                    | 7+                            |
| 对象存储     | MinIO / 本地文件系统                       | MinIO 最新                    |
| 深度学习     | PyTorch + torchvision + timm + ultralytics | 2.2 / 0.17 / 0.9.12 / 8.0.196 |
| 异步任务     | Celery                                     | 5.3.6+                        |
| 包管理       | uv (后端) / npm (前端)                     | uv 0.9+                       |
| 容器化       | Docker + Docker Compose                    | 20.10+                        |

### 3.2 仓库结构

```
thesis-image-annotation/
├── backend/                 # FastAPI 后端 (Python 3.10+)
│   ├── app/
│   │   ├── admin/           # 用户/统计/团队/系统 (v3.0 多应用架构)
│   │   ├── auth/            # 认证 (登录/注册/Token)
│   │   ├── tasks/           # 数据集/图像/训练/模型/检测/分割
│   │   ├── annotation/      # BBox / mask 标注
│   │   ├── common/          # 接口规范/枚举/工具
│   │   ├── core/            # 配置/安全/CLI/启动分析
│   │   ├── database/        # SQLAlchemy 引擎/Redis
│   │   ├── middleware/      # CORS/RequestID/错误处理
│   │   ├── schemas/         # Pydantic DTO
│   │   ├── registry.py      # App/Middleware/Plugin Registry
│   │   └── main.py          # 入口
│   ├── tests/               # pytest 套件 (43 文件)
│   ├── docs/                # 后端历史报告/规划
│   ├── models/              # 训练产物 (gitignore)
│   ├── uploads/             # 上传文件 (gitignore)
│   ├── pyproject.toml       # uv 单一来源
│   ├── requirements.txt     # pip 兼容备份
│   ├── Dockerfile.api       # API 镜像
│   ├── Dockerfile.worker    # Worker 镜像
│   ├── start_api.py         # API 启动入口 (前台/detach/PID)
│   ├── start_workers.py     # Worker 启动入口
│   └── README.md
├── frontend/                # Vue 3 前端
│   ├── src/
│   │   ├── api/             # HTTP 客户端 (与后端 1:1)
│   │   ├── composables/     # 复用逻辑 (useTrainingSsePool 等)
│   │   ├── router/          # 路由 + 权限守卫
│   │   ├── stores/          # Pinia 状态
│   │   ├── styles/          # 全局样式
│   │   ├── utils/           # 工具函数 (sse/date/format)
│   │   └── views/           # 12 个顶级页面
│   │       ├── Login/       Dashboard/  Datasets/
│   │       ├── DatasetDetail/  Annotate/  Training/
│   │       ├── Models/  Admin/  Profile/  Layout/
│   ├── package.json
│   ├── vite.config.ts
│   └── tsconfig.json
├── docs/                    # 项目级文档
│   ├── 项目说明文档.md       # 项目实现价值
│   ├── 使用手册.md          # 完整使用指南
│   ├── README.md
│   ├── 代码优化迭代记录.md
│   ├── cleanup-sprint-2026-07-29.md
│   └── ... (历史 release notes)
├── scripts/                 # 启停 / E2E 脚本
├── logs/                    # 运行时日志 (gitignore)
├── docker-compose.yml       # 一键启动编排
└── README.md                # ← 本文件
```

### 3.3 部署架构（生产形态）

```
┌─────────────────────────────────────────────────────────────┐
│                    Nginx / 反向代理 (可选)                    │
└──────┬──────────────┬──────────────┬────────────────────────┘
       │              │              │
       ▼              ▼              ▼
┌──────────┐   ┌──────────┐   ┌──────────────┐
│  前端    │   │  API     │   │  SSE / 文件   │
│ (5173)   │   │ (8000)   │   │  (同 8000)    │
└──────────┘   └────┬─────┘   └──────────────┘
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
   ┌────────┐ ┌────────┐ ┌─────────┐
   │ MySQL  │ │ Redis  │ │  MinIO  │
   │  3306  │ │  6379  │ │ 9000/01 │
   └────────┘ └───┬────┘ └─────────┘
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
   ┌─────────────┐   ┌──────────────┐
   │ worker-train│   │worker-annotate│
   │ (CPU/GPU)   │   │ (I/O 密集)   │
   │ conc=1      │   │ conc=4       │
   └─────────────┘   └──────────────┘
```

---

## 四、环境要求

### 4.1 硬件最低配置

| 角色              | CPU  | 内存  | 硬盘    | GPU         |
| ----------------- | ---- | ----- | ------- | ----------- |
| API 服务          | 2 核 | 4 GB  | 20 GB   | —          |
| Worker (CPU)      | 4 核 | 8 GB  | 50 GB   | —          |
| Worker (GPU 推荐) | 4 核 | 16 GB | 100 GB  | NVIDIA 8GB+ |
| MySQL             | 2 核 | 4 GB  | 50 GB   | —          |
| Redis             | 1 核 | 1 GB  | 5 GB    | —          |
| MinIO             | 1 核 | 2 GB  | 100 GB+ | —          |

### 4.2 软件依赖

| 软件    | 最低版本   | 用途                                   |
| ------- | ---------- | -------------------------------------- |
| Python  | 3.10+      | 后端运行时                             |
| Node.js | 18+        | 前端构建                               |
| MySQL   | 8.0+       | 主数据库                               |
| Redis   | 7+         | 缓存 + Celery broker                   |
| MinIO   | 最新稳定版 | 对象存储（可选）                       |
| Docker  | 20.10+     | 容器化（推荐）                         |
| uv      | 0.9+       | Python 包管理（推荐，比 pip 快 10 倍） |

---

## 五、快速开始

### 5.0 ⭐ 方式零：一键 Docker Compose 部署（最推荐）

> **零配置、零门槛** — 任何人都能在 30 秒内完成部署。所有环境变量从**项目根 `.env`** 统一读取。

**Windows（PowerShell）**:

```powershell
# 在项目根目录执行
.\scripts\start_docker.ps1
```

**Linux / macOS**:

```bash
chmod +x scripts/start_docker.sh
./scripts/start_docker.sh
```

脚本会自动完成 6 步：检查 `.env` → 生成强随机密钥 → 校验 Docker → 构建镜像 → 启动 7 个服务 → 端到端验证。

启动后访问：

| 服务 | URL |
| --- | --- |
| 前端 | http://localhost:8080 |
| API 文档 | http://localhost:8000/docs |
| 健康检查 | http://localhost:8000/api/health |
| MinIO 控制台 | http://localhost:9001 |

**常用命令**：

```bash
# Windows:
.\scripts\start_docker.ps1 -Status       # 查看状态
.\scripts\start_docker.ps1 -Logs         # 查看日志
.\scripts\start_docker.ps1 -Stop         # 停止服务
.\scripts\start_docker.ps1 -Rebuild      # 重新构建
.\scripts\start_docker.ps1 -Reset        # 重置数据

# Linux/macOS:
./scripts/start_docker.sh --status
./scripts/start_docker.sh --logs
./scripts/start_docker.sh --stop
./scripts/start_docker.sh --rebuild
./scripts/start_docker.sh --reset
```

### 5.1 方式一：Docker Compose 手动启动

> 适合需要更多控制的高级用户

```bash
# 1) 克隆代码
git clone <repository-url>
cd thesis-image-annotation

# 2) 准备统一 .env（项目根）
cp .env.example .env
# 自动生成强随机密钥:
python scripts/gen_secrets.py --write --env-file .env

# 3) 启动全部服务
docker compose --env-file .env up -d

# 4) 查看启动状态
docker compose ps
docker compose logs -f api
```

服务启动后访问：

| 服务         | URL                              | 默认凭据                |
| ------------ | -------------------------------- | ----------------------- |
| 前端         | http://localhost:8080            | —                      |
| API 文档     | http://localhost:8000/docs       | —                      |
| 健康检查     | http://localhost:8000/api/health | —                      |
| MinIO 控制台 | http://localhost:9001            | 见 `.env`（自动生成）   |
| MySQL        | localhost:3306                   | 见 `.env`（自动生成）   |

**默认管理员账户**：

| 用户名    | 密码         | 角色        |
| --------- | ------------ | ----------- |
| `admin` | `admin123` | super_admin |

> ⚠️ **生产环境请第一时间修改默认密码！**
> 推荐用 `python backend/scripts/bootstrap_admin.py --username admin --password "新密码"` 创建/重置。

### 5.2 方式二：本地开发模式

#### 步骤 1：启动基础设施（MySQL + Redis + MinIO）

```bash
docker compose --env-file .env up -d mysql redis minio
```

#### 步骤 2：启动后端

```bash
cd backend

# 安装依赖
uv sync                          # 推荐 (含 dev 依赖)
# 或: pip install -r requirements.txt

# 配置环境变量（后端会优先读取项目根 .env，回退到 backend/.env）
# 项目根 .env 存在时无需操作
# 否则:
cp .env.example .env
# 编辑 .env: 至少改 SECRET_KEY / MYSQL_PASSWORD

# 启动 API（前台，支持热重载）
python start_api.py --reload
# 等价: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 另开终端: 启动 Celery worker（train + annotate 双队列）
python start_workers.py
```

#### 步骤 3：启动前端

```bash
cd frontend
npm install
npm run dev                      # http://localhost:5173
```

### 5.3 第一次使用流程

1. 浏览器访问 http://localhost:5173
2. 用 `admin / admin123` 登录
3. 左侧菜单「数据集」→ 右上「新建」→ 选任务类型（分类/检测/分割）
4. 进入数据集详情 → 「上传」→ 拖拽多文件
5. 「自动标注」→ 选模型 + 设阈值 → 启动
6. 左侧菜单「人工标注」→ 键盘快捷键快速标注
7. 「模型训练」→ 选数据集 + 基础模型 → 启动
8. 数据集详情 → 「导出」→ 选 COCO / YOLO / CSV

---

## 六、配置说明

### 6.0 统一 .env 配置（v3.3.1 新设计）

> ⚠️ **核心变更**: 系统现已统一从**项目根 `.env`** 读取所有环境变量。
> 不再使用 `backend/.env.docker` / `backend/.env.prod` 等多个分散文件。

```
<项目根>/.env          ← 唯一的配置入口 (Single Source of Truth)
        ↓
   ┌────┴────┬────────────┐
   ↓         ↓            ↓
Docker    Backend      Frontend
Compose  (pydantic)   (Vite ARG)
```

- **Docker Compose**：`env_file: - .env`
- **后端**：pydantic-settings 自动从项目根 `.env` 加载（兼容 `backend/.env` 回退）
- **前端**：构建时通过 `ARG VITE_API_BASE_URL` 注入

完整字段说明见 [使用手册 § 3.1](docs/使用手册.md#三系统配置)。

### 6.1 必填项（生产环境强制）

```ini
APP_ENV=production
SECRET_KEY=<随机字符串, 至少 32 字符>     # JWT 签名密钥
MYSQL_PASSWORD=<强密码>                    # 不能用 root123/root/空
```

### 6.2 数据库

```ini
DATABASE_URL=mysql+aiomysql://user:password@host:3306/dbname
# 或分散配置
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=root123
MYSQL_DATABASE=image_annotation
```

### 6.3 Redis & Celery

```ini
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
CELERY_WORKER_POOL=threads                 # Windows 推荐 threads
CELERY_WORKER_CONCURRENCY=2
```

### 6.4 存储

```ini
STORAGE_BACKEND=minio                       # local | minio
UPLOAD_DIR=./uploads
MODEL_DIR=./models
```

### 6.5 ML 推理

```ini
INFERENCE_DEVICE=cpu                        # cpu | cuda
DEFAULT_MODEL=efficientnet_b0
DEFAULT_CONFIDENCE_THRESHOLD=0.6
```

### 6.6 HuggingFace 镜像（国内网络必配）

```ini
HF_ENDPOINT=https://hf-mirror.com
HF_HUB_DISABLE_SYMLINKS=1
HF_HUB_DISABLE_SYMLINKS_WARNING=1
```

> 详细全字段说明、生产环境 checklist 见 [使用手册](docs/使用手册.md)。

### 6.7 前端配置

> 生产模式（一键部署）：`VITE_API_BASE_URL=` 空 → 走 nginx 反代 `/api`，无需配置
> 开发模式（`npm run dev`）：在 `frontend/.env.development` 配置 `VITE_API_BASE_URL=http://localhost:8000`

### 6.8 生产环境 Checklist

部署到生产前请确认：

- [ ] `APP_ENV=production`
- [ ] `SECRET_KEY` 随机 32+ 字符
- [ ] `MYSQL_PASSWORD` 强密码（用 `python scripts/gen_secrets.py --write` 自动生成）
- [ ] `CORS_ORIGINS` 配置为实际前端域名
- [ ] `STORAGE_BACKEND=minio`（生产推荐）
- [ ] `INFERENCE_DEVICE=cuda`（如有 GPU）
- [ ] 关闭 `APP_DEBUG`
- [ ] 数据库备份策略就绪
- [ ] 日志收集方案就绪

启动时若 `APP_ENV=production` 且任一不合规，进程会**直接 fail-fast** 拒绝启动。

---

## 七、核心业务流程

### 7.1 分类任务流

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  1.上传图片  │ -> │ 2.AI 自动标注 │ -> │ 3.人工修正    │ -> │ 4.增量训练   │
│  (批量拖拽)  │    │ (timm 推理)   │    │ (键盘标注)    │    │ (Fine-tune)  │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
                            │                    │                    │
                            ▼                    ▼                    ▼
                    置信度 ≥ 阈值        更新 final_label       训练新版本
                    自动标 ai_labeled    写入 audit_log         可对比激活
                            │
                  置信度 < 阈值
                            ▼
                    推送给人工 (pending)
```

### 7.2 检测 / 分割任务流

```
上传图片 (YOLO label / mask PNG)
   ↓
启动预标注 (YOLOv8 / DeepLabV3+)
   ↓
Celery 异步推理 → SSE 实时进度
   ↓
结果入库 (BBoxAnnotation / SegmentationMask)
   ↓
人工修正 (整图重画 / 单条调整)
   ↓
训练 (YOLO train / DeepLabV3+ train)
   ↓
导出 (COCO / YOLO)
```

### 7.3 平台核心能力总结

1. **AI 预标注 + 人工修正的协同机制** —— 通过置信度阈值实现"AI 节省时间"的可量化
2. **增量训练闭环** —— 人工修正的数据自动反哺训练集
3. **多模型对比框架** —— ResNet / EfficientNet / ConvNeXt / ViT / YOLOv8 横向对比
4. **端到端实时观测** —— SSE 进度推送 + 训练曲线 + 审计日志
5. **团队协作机制** —— 数据集共享 + 角色权限 + 审计

---

## 八、API 概览

完整 OpenAPI 文档：启动后访问 http://localhost:8000/docs

### 8.1 主要端点

| 模块   | 路径                                          | 方法             | 说明                  |
| ------ | --------------------------------------------- | ---------------- | --------------------- |
| 认证   | `/api/auth/login`                           | POST             | 用户登录              |
| 认证   | `/api/auth/register`                        | POST             | 用户注册              |
| 认证   | `/api/auth/logout`                          | POST             | 撤销 Token            |
| 认证   | `/api/auth/me`                              | GET              | 当前用户              |
| 数据集 | `/api/datasets`                             | GET/POST         | 列出/创建             |
| 数据集 | `/api/datasets/{id}`                        | GET/DELETE       | 详情/删除             |
| 数据集 | `/api/datasets/{id}/categories`             | GET/POST         | 类别管理              |
| 图像   | `/api/images/upload/{dataset_id}`           | POST             | 批量上传              |
| 图像   | `/api/images/auto-label/{dataset_id}`       | POST             | AI 预标注             |
| 图像   | `/api/images/list/{dataset_id}`             | GET              | 图片列表              |
| 图像   | `/api/images/{id}`                          | GET/DELETE       | 详情/删除             |
| 标注   | `/api/annotations/save`                     | POST             | 保存标注              |
| 标注   | `/api/annotations/clear`                    | POST             | 批量清除              |
| 标注   | `/api/annotations/mark-unqualified`         | POST             | 标记不合格            |
| 标注   | `/api/annotations/stats/{dataset_id}`       | GET              | 标注统计              |
| 检测   | `/api/detection/annotations/save`           | POST             | BBox 保存             |
| 检测   | `/api/detection/annotations/{image_id}`     | GET              | 拉取 BBox             |
| 检测   | `/api/detection/train`                      | POST             | YOLO 训练             |
| 检测   | `/api/detection/auto-annotate`              | POST             | 自训练 YOLO 自动标注  |
| 检测   | `/api/detection/auto-annotate-pretrained`   | POST             | 预训练 YOLO 自动标注  |
| 分割   | `/api/segmentation/masks/upload/{image_id}` | POST             | mask 上传             |
| 分割   | `/api/segmentation/train`                   | POST             | 分割训练              |
| 训练   | `/api/training/start`                       | POST             | 启动训练              |
| 训练   | `/api/training/jobs`                        | GET              | 任务列表（分页+过滤） |
| 训练   | `/api/training/jobs/{id}`                   | GET/PATCH/DELETE | 详情/编辑/删除        |
| 训练   | `/api/training/jobs/{id}/start`             | POST             | 重跑/恢复             |
| 训练   | `/api/training/jobs/{id}/pause`             | POST             | 暂停                  |
| 训练   | `/api/training/jobs/{id}/cancel`            | POST             | 取消                  |
| 训练   | `/api/training/progress/stream/{task_id}`   | GET (SSE)        | 实时进度              |
| 训练   | `/api/training/history/{task_id}`           | GET              | 训练历史曲线          |
| 模型   | `/api/models/`                              | GET              | 模型列表              |
| 模型   | `/api/models/active`                        | GET              | 激活模型              |
| 模型   | `/api/models/{id}/activate`                 | POST             | 激活                  |
| 模型   | `/api/models/{id}/deactivate`               | POST             | 取消激活              |
| 模型   | `/api/models/batch-activate`                | POST             | 批量激活              |
| 模型   | `/api/models/batch-delete`                  | POST             | 批量删除              |
| 导出   | `/api/export/coco/{dataset_id}`             | GET              | 导出 COCO             |
| 导出   | `/api/export/yolo/{dataset_id}`             | GET              | 导出 YOLO             |
| 导出   | `/api/export/csv/{dataset_id}`              | GET              | 导出 CSV              |
| 统计   | `/api/stats/overview`                       | GET              | 全局概览              |
| 统计   | `/api/stats/dataset/{id}`                   | GET              | 数据集维度            |
| 统计   | `/api/stats/confidence/{id}`                | GET              | 置信度分布            |
| 统计   | `/api/stats/timeline/{id}`                  | GET              | 时间线                |
| 统计   | `/api/stats/annotator-efficiency`           | GET              | 标注员效率            |
| 团队   | `/api/teams`                                | GET/POST         | 团队列表/创建         |
| 团队   | `/api/teams/{id}`                           | GET/PATCH/DELETE | 详情/编辑/删除        |
| 团队   | `/api/teams/{id}/members`                   | POST             | 邀请成员              |
| 团队   | `/api/teams/{id}/members/{user_id}`         | DELETE           | 移除成员              |
| 用户   | `/api/users`                                | GET/POST         | 用户列表/创建 (admin) |
| 用户   | `/api/users/{id}/role`                      | PATCH            | 改角色 (admin)        |
| 用户   | `/api/users/{id}/reset-password`            | POST             | 重置密码 (admin)      |
| 用户   | `/api/users/{id}/activate`                  | POST             | 激活 (admin)          |
| 用户   | `/api/users/{id}/deactivate`                | POST             | 停用 (admin)          |
| 系统   | `/api/health`                               | GET              | 健康检查              |
| 系统   | `/api/system/info`                          | GET              | 系统信息              |
| 文件   | `/api/files/{id}`                           | GET              | 原图 (query token)    |
| 文件   | `/api/files/{id}/thumbnail?size=`           | GET              | 缩略图 (query token)  |

---

## 九、运维与维护

### 9.1 启动 / 停止服务

#### 一键脚本模式（推荐）

```powershell
# Windows:
.\scripts\start_docker.ps1                  # 启动
.\scripts\start_docker.ps1 -Status          # 状态
.\scripts\start_docker.ps1 -Logs            # 日志
.\scripts\start_docker.ps1 -Stop            # 停止
.\scripts\start_docker.ps1 -Rebuild         # 重建
.\scripts\start_docker.ps1 -Reset           # 重置数据
.\scripts\start_docker.ps1 -Verify          # 验证
```

```bash
# Linux/macOS:
./scripts/start_docker.sh                    # 启动
./scripts/start_docker.sh --status
./scripts/start_docker.sh --logs
./scripts/start_docker.sh --stop
./scripts/start_docker.sh --rebuild
./scripts/start_docker.sh --reset
./scripts/start_docker.sh --verify
```

#### 手动 Docker Compose 模式

```bash
# 启动全部（统一从项目根 .env 读取）
docker compose --env-file .env up -d
docker compose --env-file .env up -d api worker-train
docker compose --env-file .env ps
docker compose --env-file .env logs -f api
docker compose --env-file .env restart api
docker compose --env-file .env down
docker compose --env-file .env down -v        # 停止并清数据
```

#### 本地开发模式

```bash
# API
cd backend
python start_api.py start         # 后台启动 (写 PID)
python start_api.py --status      # 状态
python start_api.py stop          # 停止
python start_api.py --reload      # 前台 + 热重载 (开发)

# Worker
cd backend
python start_workers.py start     # 后台双队列 worker
python start_workers.py stop
python start_workers.py --status
```

### 9.2 数据库迁移

```bash
cd backend
alembic revision --autogenerate -m "描述变更"     # 生成迁移
alembic upgrade head                              # 应用迁移
alembic downgrade -1                              # 回滚一步
```

### 9.3 数据备份

```bash
# 备份前先查看数据库密码（在项目根 .env 中）
grep MYSQL_ROOT_PASSWORD .env

# MySQL 备份
docker exec annotation_mysql mysqldump -uroot -p"$(grep MYSQL_ROOT_PASSWORD .env | cut -d= -f2)" image_annotation > backup_$(date +%Y%m%d).sql

# MySQL 恢复
cat backup_20260801.sql | docker exec -i annotation_mysql mysql -uroot -p"$(grep MYSQL_ROOT_PASSWORD .env | cut -d= -f2)" image_annotation

# 文件备份
tar -czf uploads_$(date +%Y%m%d).tar.gz uploads/
tar -czf models_$(date +%Y%m%d).tar.gz models/   # 训练产物（可能很大）
```

### 9.4 健康检查

```bash
# 完整健康 (数据库/Redis/MinIO)
curl http://localhost:8000/api/health
# {"status": "ok", "db": "ok", "redis": "ok", "minio": "ok"}

# 仅 API 进程存活
curl http://localhost:8000/
```

### 9.5 性能调优

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

### 9.6 升级

```bash
# 一键脚本升级
git pull
.\scripts\start_docker.ps1 -Rebuild     # Windows
./scripts/start_docker.sh --rebuild      # Linux/macOS

# 或手动升级
git pull
cd backend
uv sync
alembic upgrade head
docker compose --env-file .env build
docker compose --env-file .env up -d
```

### 9.7 监控与日志

| 组件   | 日志位置                          |
| ------ | --------------------------------- |
| API    | `backend/logs/api.log`          |
| Worker | `backend/logs/worker_*.log`     |
| Docker | `docker compose logs <service>` |

慢请求监控：`REQUEST_SLOW_THRESHOLD_MS=500`（默认），超过则 WARNING 日志。

---

## 十、常见问题

### 10.1 安装与启动

**Q: 完全没用过 Docker，能部署吗？**
A: 能。本系统提供了一键启动脚本（`scripts/start_docker.ps1` / `scripts/start_docker.sh`），只需安装 Docker Desktop 4.x+，然后在项目根目录运行一行命令即可。脚本会自动完成配置、密钥生成、镜像构建、服务启动、端到端验证全部 6 步。详见 [§ 5.0 一键部署](#50-方式零一键-docker-compose-部署最推荐)。

**Q: Docker 启动后 API 一直重启？**
A: 查看 `docker compose logs api`，通常为数据库未就绪或 .env 配错。运行 `.\scripts\start_docker.ps1 -Logs` 可查看实时日志。

**Q: Windows 上 Celery 报 `ValueError: not enough values to unpack`？**
A: Windows 不支持 prefork，确保 `CELERY_WORKER_POOL=threads`（项目根 `.env` 中已默认设置）。

**Q: SECRET_KEY 怎么生成？**

```bash
# 自动: 由 start_docker 脚本调用 scripts/gen_secrets.py
python scripts/gen_secrets.py --write --env-file .env

# 手动:
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Q: 找不到 .env 文件？**
A: 系统已统一从**项目根 `.env`** 读取（不是 `backend/.env`）。如果不存在，复制 `.env.example` 为 `.env` 即可。

### 10.2 数据与标注

**Q: 标注完没看到"已标注"数减少？**
A: 检查 `image.status` 字段。BBox 保存会自动从 `pending/ai_labeled` 升级到 `human_confirmed`。

**Q: 删除图片后磁盘空间没释放？**
A: 容器模式需进入容器内清理；本地模式 `uploads/` 需手动清理。

**Q: 数据集共享给团队后成员看不到？**
A: 确认成员已被邀请到团队，且数据集已共享给该团队，刷新页面。

### 10.3 训练

**Q: 训练任务一直 PENDING 不动？**
A: 检查 `worker-train` 容器是否运行：`docker compose ps`，查看 `docker compose logs worker-train`。

**Q: 训练报错 "No module named 'ultralytics'"？**
A: `uv sync` 或 `pip install ultralytics==8.0.196`。

**Q: YOLO 训练时 GPU 显存不足？**
A: 调小 `batch`（如 8 → 4），或换用更小的模型（yolov8n）。

### 10.4 模型与导出

**Q: 自动标注不调用我训练的模型？**
A: 确认模型已激活 (`POST /api/models/{id}/activate`)，启动时 `model_id` 传该 id。

**Q: YOLO 导出后训练报错？**
A: 检查 `data.yaml` 的路径（建议用绝对路径），确认 `classes.txt` 数量与数据集类别一致。

### 10.5 性能

**Q: 图片列表加载慢？**
A: 加分页参数 (`page`, `page_size`)，缩略图已 Redis 缓存。

**Q: SSE 频繁断连？**
A: 检查反向代理（nginx）的 `proxy_buffering off` 与超时设置。

**Q: 训练时 CPU 100%？**
A: 正常现象，调小 `CELERY_WORKER_CONCURRENCY` 可缓解。

### 10.6 安全

**Q: 生产环境默认密码还能登录吗？**
A: `APP_ENV=production` 启动时会检查默认值，发现默认值直接拒绝启动。务必修改。

**Q: 怎么撤销已签发的 Token？**
A: 调用 `POST /api/auth/logout` 写黑名单，或修改 `SECRET_KEY` 强制全部失效。

---

## 十一、版本演进

| 版本 | 关键能力                                                                  |
| ---- | ------------------------------------------------------------------------- |
| v1.0 | 基础认证、分类数据集、上传、预标注、人工修正                              |
| v2.0 | 训练全流程、模型版本、COCO/YOLO/CSV 导出、审计日志                        |
| v2.5 | 目标检测 YOLOv8、语义分割 DeepLabV3+、不合格图片标记、SSE                 |
| v2.6 | 训练暂停/恢复、日志持久化、训练列表分页与多维过滤                         |
| v3.0 | 多应用架构重构（admin / auth / tasks / annotation）、中间件注册、插件化   |
| v3.1 | 后端 worker 4 阶段优化 + 前端训练性能 4 阶段优化（池化/节流/细粒度/联动） |
| v3.2 | 多租户、用户/角色/审计 admin 页面、显式 CORS                              |
| v3.3 | 团队管理、MinIO 存储后端、路径锚定项目根、ultralytics 缓存收敛            |
| v3.3.1 | **统一 .env 配置（项目根 Single Source of Truth）** + 一键启动脚本 (`start_docker.ps1` / `start_docker.sh`) |

详细变更记录：[docs/代码优化迭代记录.md](docs/代码优化迭代记录.md) / [docs/cleanup-sprint-2026-07-29.md](docs/cleanup-sprint-2026-07-29.md)

---

## 十二、许可

本项目为商业可用的图像自动标注与人机协同修正系统，基于 **MIT 协议**开源，仅供学习交流。

---

## 附录

- [后端 README](backend/README.md) — 后端模块详细说明
- [项目说明文档](docs/项目说明文档.md) — 项目实现价值客观描述
- [使用手册](docs/使用手册.md) — 完整使用指南
- [API 文档](http://localhost:8000/docs) — 启动后访问
- [健康检查](http://localhost:8000/api/health) — 启动后访问
