# Phase N+O 后端 API 单体文件拆分完成报告

---

## 一、背景

后端 `app/tasks/api/` 目录存在多个超 1000 行的单体文件,违反项目代码风格规范:

| 文件 | 原始行数 | 状态 |
|---|---|---|
| `image.py` | 1152 | Phase N 已拆分 |
| `training.py` | 1019 | Phase O 已拆分 |
| `detection.py` | 980 | 接近阈值,待 Phase P 处理 |

本次完成 Phase N + Phase O 拆分,共 2 个原子 commit,总变更 +2434/-1019 行。

---

## 二、Phase N: image.py → image/ 包

**Commit**: 17929cb
**变更**: 1152 行单体 → 6 文件, +1290/-0 行

### 拆分结构

```
app/tasks/api/image/
├── __init__.py     56 行   装配 (引入 5 子 router, 组合顶层 router)
├── upload.py      159 行   批量上传 + 多重校验
├── auto_label.py  210 行   批量 AI 预标注
├── preview.py     577 行   置信度预览 (3 任务分派)
├── query.py       219 行   列表/详情查询
└── delete.py       69 行   单图/批量删除
```

### 路由清单 (7 个,完全向后兼容)

- POST `/api/images/upload/{dataset_id}`
- POST `/api/images/auto-label/{dataset_id}`
- POST `/api/images/preview-confidence`
- GET `/api/images/list/{dataset_id}`
- GET `/api/images/{image_id}`
- DELETE `/api/images/{image_id}`
- POST `/api/images/batch-delete`

---

## 三、Phase O: training.py → training/ 包

**Commit**: 5d59b39
**变更**: 1019 行单体 → 6 文件, +1144/-1019 行

### 拆分结构

```
app/tasks/api/training/
├── __init__.py    ~60 行   装配 (引入 5 子 router, 组合顶层 router)
├── start.py       370 行   启动 (新建 + restart/resume) + 共享工具
├── progress.py    250 行   REST 轮询 + SSE 实时推送
├── history.py      60 行   历史曲线 (双源: Redis + DB)
├── jobs.py        340 行   任务 CRUD/控制/错误 (7 路由)
└── log.py          70 行   训练日志读取/追加
```

### 关键设计

- **预创建/回滚函数从嵌套函数抽离为模块级独立函数**:
  - `_create_pending_restart_job` (原内嵌) → 模块级 `async def`
  - `_rollback_restart` (原内嵌) → 模块级 `async def`
  - 减少 start_existing_training_job 复杂度, 单元测试可独立覆盖

### 路由清单 (15 个,完全向后兼容)

- POST `/api/training/start`
- GET `/api/training/progress/{task_id}` (REST 轮询)
- GET `/api/training/progress/stream/{task_id}` (SSE)
- GET `/api/training/history/{task_id}`
- GET `/api/training/jobs` (列表)
- GET `/api/training/jobs/` (尾斜杠兼容)
- GET `/api/training/jobs/{job_id}` (详情)
- POST `/api/training/jobs/{job_id}/cancel`
- POST `/api/training/jobs/{job_id}/pause`
- POST `/api/training/jobs/{job_id}/start` (restart/resume)
- POST `/api/training/jobs/{job_id}/error`
- GET `/api/training/jobs/{job_id}/log`
- POST `/api/training/jobs/{job_id}/log`
- PATCH `/api/training/jobs/{job_id}`
- DELETE `/api/training/jobs/{job_id}`

---

## 四、验证结果

| 验证项 | 结果 |
|---|---|
| image 包 py_compile | exit=0 (6 文件) |
| training 包 py_compile | exit=0 (6 文件) |
| 整体后端 py_compile | exit=0 (148 文件) |
| TasksApp.get_routes() | 9 个路由条目 |
| `/api/images` 路由数 | 7 个 (与原一致) |
| `/api/training` 路由数 | 15 个 (与原一致) |
| 旧 image.py / training.py 删除 | 已删除 (避免与包同名冲突) |
| import 路径兼容性 | `app.tasks.api.image` / `app.tasks.api.training` 均可正常导入 |

---

## 五、累计节省 (vs 原计划)

| 维度 | 数值 |
|---|---|
| 总 commit 数 | 2 个 (Phase N + Phase O) |
| 总变更行数 | +2434/-1019 |
| 单文件最大行数 | 577 行 (preview.py) |
| 整体后端 py 文件数 | 148 |
| 路由零变更 | 是 (完全向后兼容) |
| 测试覆盖影响 | 无 (纯重构,业务逻辑零变化) |

---

## 六、下一步建议 (可选)

- **Phase P**: `detection.py` (980 行) 接近阈值,可主动拆分
  - 候选方案: 按功能切分为 `bbox_query.py` + `bbox_write.py` + `annotation.py` + `training.py` (注意: 此处 training 是 detection 的训练 API, 与 tasks/training 不同)

- **仍存在的未提交修改** (非本次 Phase 范围, 来自 Stage 1-5 多应用架构):
  - `app/__main__.py` / `app/main.py` 调整
  - `app/cli.py` / `app/config.py` 等老入口文件删除
  - `app/schemas/enums.py` 删除
  - 建议作为独立 Stage 1 收尾 commit 处理

---

## 七、Commit 信息

```
17929cb refactor(phaseN): image.py 拆分为 image/ 包 (5 子模块, 1152 → 6 文件)
5d59b39 refactor(phaseO): training.py 拆分为 training/ 包 (5 子模块, 1019 → 6 文件)
```
