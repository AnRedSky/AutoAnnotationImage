# 图像自动标注系统 - 后端服务

基于 FastAPI + MySQL + Redis + MinIO + Celery 的图像自动标注后端服务。

> 本项目使用 [uv](https://github.com/astral-sh/uv) 进行依赖管理。

## 环境要求

- Python 3.10+
- uv 0.9+ ([安装](https://github.com/astral-sh/uv))
- MySQL 8.0+
- Redis 7+
- MinIO（可选）

## 快速开始

### 1. 安装依赖

```bash
# 创建虚拟环境并安装依赖
uv sync

# 仅安装运行时依赖（不含 dev）
uv sync --no-dev
```

### 2. 启动开发服务

```bash
# 激活虚拟环境
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate  # Windows

# 复制环境变量模板
cp .env.example .env

# 启动 FastAPI 开发服务器
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. 启动 Celery Worker（可选）

```bash
uv run celery -A app.workers.celery_app worker --loglevel=info
```

### 4. Docker 部署

```bash
docker build -t image-annotation-backend .
docker run -p 8000:8000 image-annotation-backend
```

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `uv sync` | 安装/同步所有依赖 |
| `uv add <pkg>` | 添加新依赖 |
| `uv remove <pkg>` | 移除依赖 |
| `uv run <cmd>` | 在虚拟环境中执行命令 |
| `uv lock` | 更新锁定文件 |
| `uv tree` | 查看依赖树 |
| `uv run pytest` | 运行测试 |
| `uv run ruff check` | 代码检查 |
| `uv run ruff format` | 代码格式化 |

## 项目结构

```
backend/
├── app/
│   ├── api/         # RESTful API 路由
│   ├── core/        # 核心配置（数据库、鉴权、安全）
│   ├── ml/          # 机器学习模型
│   ├── models/      # SQLAlchemy ORM 模型
│   ├── schemas/     # Pydantic 数据模型
│   ├── services/    # 业务服务层
│   ├── workers/     # Celery 异步任务
│   ├── config.py    # 应用配置
│   ├── database.py  # 数据库连接
│   └── main.py      # FastAPI 入口
├── tests/           # 测试代码
├── pyproject.toml   # 项目配置（uv/pip 通用）
├── uv.lock          # 依赖锁定文件
├── requirements.txt # Docker 构建用的 pip 备份
├── Dockerfile       # 容器镜像构建
└── .env.example     # 环境变量模板
```

## 注意事项

- 本项目使用 uv 进行本地依赖管理
- Docker 部署仍使用 `requirements.txt`（保证镜像构建兼容性）
- 提交代码时请同时提交 `pyproject.toml` 和 `uv.lock`
