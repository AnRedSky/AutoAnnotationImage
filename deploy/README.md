# deploy/ — 部署集中管理目录

> **版本**: v3.3.2 (2026-08-01)
> **状态**: 🟢 生产就绪 — 7/7 服务健康运行

---

## 目录结构

```
deploy/
├── README.md                          ← 本文件 (目录索引)
├── configs/                           ← 配置文件参考副本
│   ├── docker-compose.reference.yml   ← docker-compose.yml 参考副本
│   ├── .env.example                   ← 环境变量模板
│   ├── Dockerfile.backend             ← 后端主 Dockerfile (含 api/worker/dev)
│   ├── Dockerfile.api                 ← API 专用 Dockerfile
│   ├── Dockerfile.worker              ← Worker 专用 Dockerfile
│   ├── Dockerfile.frontend            ← 前端 Dockerfile
│   ├── docker-entrypoint.sh           ← 容器入口脚本 (修复卷权限 + 降权)
│   ├── nginx.conf                     ← Nginx 反代配置
│   ├── .dockerignore.backend          ← 后端构建上下文排除
│   └── .dockerignore.frontend         ← 前端构建上下文排除
├── scripts/                           ← 部署脚本
│   ├── start_docker.ps1               ← Windows 一键部署
│   ├── start_docker.sh                ← Linux/macOS 一键部署
│   ├── gen_secrets.py                 ← 密钥自动生成
│   ├── verify_deployment.py           ← 端到端部署验证
│   ├── verify_deployment.ps1          ← PowerShell 验证包装
│   └── verify_inter_service.ps1       ← 跨服务通信验证
├── docs/                              ← 部署文档
│   └── 部署指南.md                    ← 完整部署操作手册
└── reports/                           ← 检测报告
    └── 部署深度检测报告.md            ← v3.3.2 深度检测报告
```

## 快速开始

```bash
# 1. 回到项目根目录
cd d:\works\WorkBuddy\Myhome\ThesisDesignImplementation\thesis-image-annotation

# 2. 一键部署 (Windows)
.\scripts\start_docker.ps1

# 3. 一键部署 (Linux/macOS)
./scripts/start_docker.sh

# 4. 验证
.\scripts\verify_inter_service.ps1
```

## 注意事项

- **配置文件为参考副本**: 实际构建使用项目根目录的 `docker-compose.yml` 和 `backend/`、`frontend/` 下的 Dockerfile
- **.env 不在此目录**: 实际 `.env` 位于项目根目录 (已在 .gitignore 排除)
- **修改配置时**: 请修改项目根目录的原始文件, 然后同步到本目录
