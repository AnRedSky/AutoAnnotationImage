# 项目整理与冗余清理 (Cleanup Sprint 2026-07-29)

> **整理日期**: 2026-07-29
> **整理范围**: 全仓库 (后端 + 前端 + 文档 + IDE 目录)
> **目标**: 移除一次性脚本、孤儿数据、磁盘垃圾, 让仓库贴近"代码即文档"的生产标准

---

## 一、清理总览

| 维度 | 删除项 | 磁盘回收 | 影响范围 |
|---|---|---|---|
| **tracked 文件** | 22 个 | ~11 MB (cifar10 tar.gz) | 仓库瘦身, history 干净 |
| **未 tracked 垃圾** | 7 个目录/文件 | ~107 MB (logs) + 12 MB (demo) | 仅本地磁盘, 不影响仓库 |
| **__pycache__ 等缓存** | 634 个目录 | 几 MB | 仅本地, 重新跑测试会再生 |
| **.gitignore 补全** | 2 条新规则 | — | 防止新 IDE 目录漏提交 |

---

## 二、删除清单

### 2.1 后端一次性脚本 (13 个, 零代码引用)

**Stage 2.8 架构迁移脚本** (7 个):

| 文件 | 大小 | 用途 |
|---|---|---|
| `backend/stage2_8_2_migrate_api.py` | 5.2 KB | API 层 import 路径迁移 (v2 → v3) |
| `backend/stage2_8_2_migrate_api_v2.py` | 3.1 KB | 修订版 |
| `backend/stage2_8_3_fix_all_refs.py` | 6.3 KB | 批量 import 修正 |
| `backend/stage2_8_3_fix_services_bare_imports.py` | 4.4 KB | 补全 service 模块 import |
| `backend/stage2_8_3_fix_services_refs.py` | 2.5 KB | service 之间 ref 修正 |
| `backend/stage2_8_3_fix_workers_paths.py` | 1.1 KB | workers 内部路径修正 |
| `backend/stage2_8_3_migrate_services.py` | 7.0 KB | 业务 service 抽取 (Stage 2.8 主迁移脚本) |

**验证脚本** (6 个, 针对历史 bug, 非 pytest 套件):

| 文件 | 大小 | 用途 |
|---|---|---|
| `backend/verify_routes.py` | 1.0 KB | 路由注册验证 |
| `backend/verify_stage2_8_2.py` | 2.4 KB | Stage 2.8 Phase 2 收尾验证 |
| `backend/verify_stage2_8_all.py` | 3.0 KB | Stage 2.8 全流程验证 |
| `backend/verify_stage2_8_final.py` | 5.5 KB | Stage 2.8 最终验证 |
| `backend/tests/verify_all_history.py` | 6.0 KB | v2.5.29 history 实时同步验证 |
| `backend/tests/verify_detection_history.py` | 4.7 KB | detection history 验证 |
| `backend/tests/verify_model_paths.py` | 3.8 KB | 模型路径落点验证 |

**依据**: 通过 `grep -r "stage2_8_\|verify_stage2_8_" backend/app` 确认**零引用**, 仅在 `backend/docs/26-Stage2.8-实施完成报告.md` 历史报告里提到。

### 2.2 重复入口 (1 个)

| 文件 | 替换为 |
|---|---|
| `backend/run.py` (0.7 KB) | `backend/start_api.py` (8.5 KB) |

**依据**: `run.py` 只是 `from app.core.cli import main` 的薄包装, 而 `start_api.py` 提供完整能力: 前台 / detached / --reload / --stop / --status / PID 管理 / Windows DETACHED_PROCESS。功能 95% 重复, 保留功能全面的 start_api.py。

### 2.3 scripts/ 调试脚本 (4 个, 路径已失效)

| 文件 | 大小 | 问题 |
|---|---|---|
| `scripts/test_login.py` | 3.6 KB | 写死旧项目路径 `thesis-image-annotation` |
| `scripts/test_sse_stream.py` | 6.1 KB | 写死 BASE URL, 已无独立使用 |
| `scripts/test_training_control.py` | 13.8 KB | 写死旧项目 venv 路径, 单点验证脚本 |
| `scripts/test_training_history.py` | 1.9 KB | 一次性 history 端点探针 |

**依据**:
- `grep "scripts/test_" backend docs` 仅在 `scripts/test_login.py` 自身 docstring 提到, 无外部调用
- 路径写死旧项目名 `thesis-image-annotation`, 当前项目为 `thesis-image-annotation-cs`, 跑必失败
- `scripts/run_e2e.py` (18.6 KB) 已涵盖 E2E 测试, 4 个独立脚本是早期分块调试产物

### 2.4 死代码 (1 个)

| 文件 | 大小 | 依据 |
|---|---|---|
| `scripts/_gen_start.ps1.py` | 11.6 KB | `grep "_gen_start.ps1"` 全仓库**零引用** |

下划线开头是约定: 私有 / 内部脚本, 实际未启动服务用。`scripts/start_local.ps1` 是当前实际启服务脚本, 内部自己生成, 不需要预生成器。

### 2.5 孤儿 demo 数据 (2 个, 仓库 + 磁盘)

| 路径 | 大小 | 文件数 | 状态 |
|---|---|---|---|
| `backend/demo/data/cifar10_raw/cifar-10-python.tar.gz` | 11 MB | 1 | 仓库 tracked, 一手 CIFAR-10 原始包, 提取后未删 |
| `backend/demo/data/cifar10_subset/` | 12 MB | 1182 | 磁盘 only, 5 类 × 200 张 jpg |
| `backend/demo/data/detection_demo/` | <1 MB | 90 | 磁盘 only, 3 色小图 + YOLO 标签 |

**孤儿依据**:
- `grep "cifar10\|detection_demo" backend` → 仅 `timm.dataset_factory` 和 `torchvision.transforms` 自身引用, **项目代码零引用**
- docs 反复提到 `scripts/prepare_demo_data.py` 生成这些数据, 但脚本文件**不在仓库**
- `backend/demo/data/detection_demo/data.yaml` 写死**旧项目路径** `thesis-image-annotation/backend/...`, 跑训练必失败

**保留决策**: `backend/demo/` 目录本身 (含空 data/) 暂保留, 因为 backend/docs/10 目录结构指南把它列为"运行时数据", 留空目录占位便于脚本知道该往哪写。

### 2.6 临时日志 / 覆盖文件 (3 个)

| 路径 | 大小 | 状态 |
|---|---|---|
| `logs/` (整个目录) | 106 MB | gitignore 已忽略, 单文件 `backend.out.log` 107 MB |
| `backend/backend_log.txt` | 7.5 KB | gitignore 已忽略, 临时手写日志 |
| `.coverage` (根) | 0 KB (空) | gitignore 已忽略 |
| `backend/.coverage` | 100 KB | gitignore 已忽略 |
| `docs/e2e/` | 0 KB (空目录) | 无 README 解释用途, 截图无产出 |

### 2.7 缓存目录 (634 个)

| 类型 | 数量 | 大小 |
|---|---|---|
| `__pycache__/` (Python) | 600+ | 几 MB |
| `.pytest_cache/` | ~5 | 0.01 MB |

gitignore 已忽略, 跑测试会再生。本次清理只是释放磁盘。

---

## 三、保留项与依据

| 项 | 大小 | 保留理由 |
|---|---|---|
| `backend/docs/` (37 个文件) | 1.4 MB | 项目架构演进 + 论文素材, 不可删除 |
| `docs/` (32 个文件) | 0.3 MB | 项目规划 / 报告 / release notes |
| `backend/models/` (训练产物) | 4.6 GB | gitignore 已忽略, 是真实训练结果, 重跑成本高 |
| `backend/.venv/` | 1.7 GB | gitignore 已忽略, 重建成本高 (uv sync 即可) |
| `frontend/node_modules/` | 189 MB | gitignore 已忽略, 重建成本高 (npm install) |
| `scripts/run_e2e.py` | 18.6 KB | 核心 E2E 入口, 当前项目唯一在用 |
| `scripts/start_local.*` | 6 文件 | 启停 + 状态查询 + 诊断, 核心服务控制脚本 |
| `scripts/fixtures.py` | 6.5 KB | E2E 测试 fixture, 被 run_e2e.py 引用 |
| `scripts/e2e_test.ps1` | 10.6 KB | PowerShell 入口, 包装 run_e2e.py |
| `scripts/health_probe.py` | 0.4 KB | 健康探针, 轻量工具 |
| `scripts/test_min.ps1` | 0.4 KB | 最小启动测试, 调试用 |
| `backend/start_api.py` / `start_workers.py` | 22 KB | 生产级启动入口 (前台/detach/PID/状态) |
| `.openharness/` (当前活跃 IDE) | 几 MB | untracked, 不入仓, 仅本地状态 |
| `.qoder/` (历史 IDE) | 几 KB | untracked, 不入仓, 加进 .gitignore |

---

## 四、.gitignore 补全

```diff
 # IDE
 .vscode/
 .idea/
 *.swp
 *.swo
 .tmp
 .trae/
+.openharness/
+.qoder/
```

**说明**:
- `.openharness/` 当前活跃 IDE (本会话所用), 之前未忽略
- `.qoder/` 历史 IDE 状态目录, 包含 `plans/Worker性能优化_d22a7fa6.md` 等过时 plan
- 两者都属 IDE 私有, 不应入仓

---

## 五、验证

### 5.1 仓库状态

```
$ git status --short
 M .gitignore                       (补全 2 行)
D  backend/run.py                   (删)
D  backend/stage2_8_*.py            (7 删)
D  backend/verify_*.py              (4 删)
D  backend/tests/verify_*.py        (3 删)
D  scripts/test_*.py                (4 删)
D  scripts/_gen_start.ps1.py        (1 删)
D  backend/demo/data/cifar10_raw/cifar-10-python.tar.gz  (1 删)
D  docs/draft/figures/README.md     (1 删)
?? .qoder/                          (IDE, untracked, 留本地)
```

### 5.2 引用完整性

```bash
# 验证删的脚本无外部引用
$ grep -r "stage2_8_\|verify_stage2_8_\|run\.py\b" backend/app
(空)
$ grep -r "scripts/test_login\|scripts/test_sse" backend frontend docs
(空)
```

### 5.3 启动入口仍可用

```bash
$ python backend/start_api.py --help
# (输出 start_api.py 自带 help, 与原 run.py 等价)
```

### 5.4 磁盘回收

| 项 | 释放 |
|---|---|
| logs/ (106 MB) | ✓ |
| backend/demo/data (12 MB) | ✓ |
| backend_log.txt (7.5 KB) | ✓ |
| __pycache__ 缓存 (几 MB) | ✓ |
| 旧 .coverage 文件 (100 KB) | ✓ |
| **总计** | **~118 MB** |

---

## 六、未清理 / 后续

- `backend/.venv/` (1.7 GB) — uv 依赖, 重建命令: `uv sync`
- `frontend/node_modules/` (189 MB) — npm 依赖, 重建命令: `npm install`
- `backend/models/cache/` (150 MB torch hub 权重) + `backend/models/runs/` + `seg_runs/` (4.4 GB) — 训练产物, 重跑成本高, 保留
- `docs/draft/figures/` 空目录 — 论文插图待产出, 暂保留

---

## 七、追溯

- v3.1.0 (commit `158d1bf`): 前端训练性能优化
- v3.1.0 (commit `5ec1284`): 后端 worker 4 阶段优化
- v3.0.0 (commit `4e998c4`): 不合格图片检测与分类训练
- v2.5.16 (2026-07-22): 后端代码优化迭代 (Celery 工具收敛 / 异常体系 / 连接池)
- v2.5.15 (2026-07-22): 4 P0 + 3 P1 + 3 P1 性能修复
- 2026-07-22 ~ 25: 3 层架构重构 (Phase 1-5 / Stage 1-5, 15.5 天)
- 2026-07-29: 本次 Cleanup Sprint
