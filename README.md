# Image Annotation System

> 基于深度学习的图像分类自动标注与人工修正系统的设计与实现
> 深圳大学本科毕业论文项目

## 系统简介

本系统实现「AI 预标注 + 人工修正」的协同标注范式，融合深度学习（PyTorch + timm）与人机交互（Vue 3 + FastAPI），让图像分类标注工作流从「全人工」提升为「AI 辅助 + 人工复核」。

## 核心特性

- **AI 自动标注**：调用 timm 预训练模型（700+ SOTA 模型）批量推理，输出 Top-5 候选标签
- **智能分流**：根据置信度阈值决定自动标注 / 人工快速确认 / 强制人工标注
- **高效人工 UI**：键盘快捷键标注、标签联想、实时计时
- **增量训练闭环**：人工修正后的数据自动加入训练集，一键 Fine-tune
- **模型版本管理**：训练历史、效果对比、可视化训练曲线
- **完整审计**：每次标注操作完整记录，支持「AI 节省时间」量化

## 技术栈

| 层级 | 选型 |
|------|------|
| 前端 | Vue 3 + Vite + TypeScript + Element Plus + ECharts + Pinia |
| 后端 | Python 3.10+ + FastAPI + SQLAlchemy 2.0 (async) |
| 数据库 | MySQL 8.0 + Redis 7 |
| 存储 | MinIO (开发) / 七牛云 (生产) |
| 深度学习 | PyTorch 2.x + timm |
| 异步任务 | Celery + Redis |
| 部署 | Docker + Docker Compose |

## 目录结构

```
thesis-image-annotation/
├── backend/                  # FastAPI 后端
│   ├── app/
│   │   ├── api/             # RESTful API 路由
│   │   ├── core/            # 核心模块 (security, deps)
│   │   ├── ml/              # 机器学习 (训练, 推理)
│   │   ├── models/          # SQLAlchemy ORM
│   │   ├── schemas/         # Pydantic 模型
│   │   ├── services/        # 业务服务
│   │   ├── workers/         # Celery 异步任务
│   │   ├── config.py
│   │   ├── database.py
│   │   └── main.py
│   ├── requirements.txt
│   └── .env.example
├── frontend/                # Vue 3 前端
│   ├── src/
│   │   ├── api/             # HTTP 客户端
│   │   ├── components/      # 公共组件
│   │   ├── router/          # 路由
│   │   ├── stores/          # Pinia 状态
│   │   ├── views/           # 页面
│   │   ├── App.vue
│   │   └── main.ts
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
├── demo/                    # 离线最小演示 (无需数据库)
│   └── demo_inference.py
├── docs/                    # 论文资料
├── docker-compose.yml       # 一键启动
├── 0号文档-邮件定题目-我的选题.md  # 论文定题文档
└── README.md
```

## 快速开始

### 方式 1: Docker 一键启动 (推荐)

```bash
# 启动 MySQL + Redis + MinIO + Backend + Celery + Frontend
docker-compose up -d

# 查看日志
docker-compose logs -f backend
```

服务启动后：
- 前端: http://localhost:5173
- 后端 API: http://localhost:8000
- API 文档: http://localhost:8000/docs
- MinIO 控制台: http://localhost:9001 (minioadmin / minioadmin)

### 方式 2: 本地开发

#### 1. 启动数据库 (任选)
```bash
# 用 Docker 起 MySQL + Redis
docker run -d --name mysql -e MYSQL_ROOT_PASSWORD=root123 -e MYSQL_DATABASE=annotation_db -p 3306:3306 mysql:8.0
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

#### 2. 启动后端
```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 配置数据库连接
uvicorn app.main:app --reload --port 8000
```

#### 3. 启动 Celery Worker (训练任务)
```bash
cd backend
celery -A app.workers.celery_app worker --loglevel=info
```

#### 4. 启动前端
```bash
cd frontend
npm install
npm run dev
```

### 方式 3: 只跑 Demo (无需任何后端)

```bash
cd demo
python demo_inference.py --offline   # 离线模式
python demo_inference.py <image.jpg>  # 指定图片
```

## 核心业务流程

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  1.上传图片  │ -> │ 2.AI 自动标注 │ -> │ 3.人工修正    │ -> │ 4.增量训练   │
│  (批量拖拽)  │    │ (timm 推理)   │    │ (键盘标注)    │    │ (Fine-tune)  │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
                            │                    │                    │
                            v                    v                    v
                    置信度 >= 0.85       更新 final_label       训练新版本
                    自动标 ai_labeled    写入 audit_log         可对比激活
                            │
                  置信度 < 0.85
                            v
                    推送给人工 (pending)
```

## 论文核心创新点

1. **AI 预标注 + 人工修正的协同机制** —— 通过置信度阈值实现"AI 节省时间"的可量化
2. **增量训练闭环** —— 人工修正的数据自动反哺训练集
3. **多模型对比框架** —— ResNet / EfficientNet / ConvNeXt / ViT 横向对比

## API 概览

| 模块 | 路径 | 方法 | 说明 |
|------|------|------|------|
| 认证 | `/api/auth/login` | POST | 用户登录 |
| 认证 | `/api/auth/register` | POST | 用户注册 |
| 数据集 | `/api/datasets/` | GET/POST | 列出/创建数据集 |
| 数据集 | `/api/datasets/{id}/categories` | GET/POST | 类别管理 |
| 图像 | `/api/images/upload/{dataset_id}` | POST | 批量上传 |
| 图像 | `/api/images/auto-label/{dataset_id}` | POST | AI 自动标注 |
| 图像 | `/api/images/list/{dataset_id}` | GET | 图片列表 |
| 标注 | `/api/annotations/save` | POST | 保存人工标注 |
| 标注 | `/api/annotations/stats/{dataset_id}` | GET | 标注效率统计 |
| 训练 | `/api/training/start` | POST | 启动训练 |
| 训练 | `/api/training/progress/{task_id}` | GET | 训练进度 |
| 模型 | `/api/models/` | GET | 模型列表 |
| 模型 | `/api/models/{id}/activate` | POST | 激活模型 |

完整 API 文档: http://localhost:8000/docs

## 论文资料

- `0号文档-邮件定题目-我的选题.md` —— 论文定题文档 (邮件给导师)
- `docs/` —— 存放开题报告、中期检查、答辩 PPT 等论文相关文档

## License

本项目为深圳大学本科毕业论文项目，仅供学习交流。
