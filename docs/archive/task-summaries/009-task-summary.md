# 任务 009 总结报告 - v3.3.4-PATCH 全面修复 (list 接口 + 单条操作 admin 旁路)

---

## 一、本次任务背景

用户在 008 报告后回测时发现: 即使 008 已修复 `assert_can_access_dataset` 等核心函数, 但仍有大量 list 接口和单条操作接口直接调用 `is_admin()` 旁路校验, 导致 **regular admin 仍可越权访问**:

- **list 接口**: `list_datasets`/`list_training_jobs`/`list_models`/`recent_annotations` 等用 `is_admin()` 直接旁路 → 任何 admin 可见全平台数据
- **单条操作接口**: `cancel_training_job`/`pause_training_job`/`delete_training_job` 等用 `is_admin() and user_id != me` 兜底, 越权保护缺失
- **训练进度接口**: `training/detection/segmentation` 三个模块的 progress 接口用 `is_admin()` 旁路
- **模型 delete/activate**: 孤儿 model (`dataset_id=NULL`) 用 `is_admin()` 兜底, 越权

### 核心设计原则 (重申)

> **数据级访问 ≠ 系统级管理** — `is_admin()` 涵盖 `super_admin` 和 `admin` 两个角色, 但**数据级访问只允许 `super_admin` 旁路**, regular `admin` 必须受团队隔离约束。

---

## 二、问题清单与修复矩阵

| # | 优先级 | 文件 | 修复点 | 状态 |
|---|--------|------|--------|------|
| P0-2 | 🔴 **P0** | `training/jobs.py::list_training_jobs` | `is_admin()` → `is_super_admin()` + team 共享过滤 | ✅ |
| P0-3 | 🔴 **P0** | `model/query.py::list_models` | `is_admin()` → `is_super_admin()` + team 共享过滤 | ✅ |
| P0-3 | 🔴 **P0** | `model/query.py::get_model_detail` | 孤儿 model 旁路 `is_admin()` → `is_super_admin()` | ✅ |
| P0-3 | 🔴 **P0** | `model/query.py::list_active_models` | `is_admin()` → `is_super_admin()` | ✅ |
| P0-4 | 🔴 **P0** | `annotation/api/annotation.py::recent_annotations` | `is_admin()` → `is_super_admin()` | ✅ |
| P0-5 | 🔴 **P0** | `training/jobs.py::delete_training_job` | 旧 `is_admin() and user_id != me` → 统一权限函数 | ✅ |
| P0-6 | 🔴 **P0** | `model/deletion.py::delete_model` | 孤儿 model 旁路 `is_admin()` → `is_super_admin()` | ✅ |
| P0-6 | 🔴 **P0** | `model/deletion.py::batch_delete_models` | 孤儿 model 旁路 `is_admin()` → `is_super_admin()` | ✅ |
| P0-6 | 🔴 **P0** | `model/activation.py::deactivate_model` | 孤儿 model 旁路 `is_admin()` → `is_super_admin()` | ✅ |
| P1   | 🟠 **P1** | `training/progress.py::_assert_can_access_task_id` | `is_admin()` → `is_super_admin()` | ✅ |
| P1   | 🟠 **P1** | `training/log.py::_assert_can_access_job` | `is_admin()` → `is_super_admin()` | ✅ |
| P1   | 🟠 **P1** | `segmentation/progress.py::2 处` | `is_admin()` → `is_super_admin()` | ✅ |
| P1   | 🟠 **P1** | `detection/progress.py::3 处` | `is_admin()` → `is_super_admin()` | ✅ |

---

## 三、核心修复策略

### 3.1 list 接口: 收紧为仅 super_admin 旁路 + 个人/团队共享过滤

**修改前 (越权)**:
```python
if not current_user.is_admin():
    own_ds = select(Dataset.id).where(Dataset.owner_id == current_user.id)
    stmt = stmt.where(ModelVersion.dataset_id.in_(own_ds))
```

**修改后 (PATCH 修复)**:
```python
if not current_user.is_super_admin():
    member_team_ids_q = (
        select(_TM.team_id)
        .join(_Team, _Team.id == _TM.team_id)
        .where(_TM.user_id == current_user.id, _Team.archived_at.is_(None))
    )
    vis_ds_filter = _or(
        Dataset.owner_id == current_user.id,
        Dataset.team_id.in_(member_team_ids_q),
    )
    stmt = stmt.where(ModelVersion.dataset_id.in_(
        select(Dataset.id).where(vis_ds_filter)
    ))
```

### 3.2 单条操作: 统一权限函数

`training/jobs.py::delete_training_job` 从内联权限检查改为调用 `assert_can_access_training_job`:
```python
# 修改前 (越权: regular admin 可删非 own)
if not current_user.is_admin() and job.user_id != current_user.id:
    raise HTTPException(403, "无权限操作此训练任务")

# 修改后 (统一: 团队成员也可操作, 不仅是 owner)
from app.tasks.service.permission_service import assert_can_access_training_job
await assert_can_access_training_job(db, current_user, job.user_id, job.dataset_id)
```

### 3.3 孤儿 model 操作: 收紧为仅 super_admin

孤儿 model (dataset_id=NULL) 无 dataset 关联, 无法走 `assert_can_access_dataset`:
- 修改前: `is_admin()` 旁路 → regular admin 可删/激活
- 修改后: `is_super_admin()` 旁路 → 仅超管可操作孤儿 model

---

## 四、回归测试矩阵 (17 项)

新建 [test_permission_v334_patch.py](file:///d:/works/WorkBuddy/Myhome/ThesisDesignImplementation/thesis-image-annotation/backend/tests/test_permission_v334_patch.py), 涵盖所有 PATCH 修复点:

| 测试编号 | 测试场景 | 期望结果 | 状态 |
|---------|---------|---------|------|
| T-P01 | regular admin `list_datasets` 看不到其他团队 dataset | 仅 own + 团队共享 | ✅ |
| T-P02 | super_admin `list_datasets` 看到全部 dataset | 全部 | ✅ |
| T-P03 | regular admin `list_training_jobs` 仅看自己+团队共享 | 隔离生效 | ✅ |
| T-P04 | super_admin `list_training_jobs` 看到全部 job | 全部 | ✅ |
| T-P05 | regular admin `list_models` 仅看自己+团队共享 | 隔离生效 | ✅ |
| T-P06 | super_admin `list_models` 看到全部 model | 全部 | ✅ |
| T-P07 | regular admin `list_active_models` 受 dataset 可见性约束 | 隔离生效 | ✅ |
| T-P08 | regular admin `get_model_detail` 失败 (别人的 model) | 403 | ✅ |
| T-P09 | regular admin `recent_annotations` 受 dataset 可见性约束 | 隔离生效 | ✅ |
| T-P10 | super_admin `recent_annotations` 看全系统 | 全部 | ✅ |
| T-P11 | regular admin `cancel_training_job` 失败 (非 own) | 403 | ✅ |
| T-P12 | regular admin `pause_training_job` 失败 (非 own) | 403 | ✅ |
| T-P13 | regular admin `get_training_log` 失败 (非 own) | 403 | ✅ |
| T-P14 | regular admin `get_training_error` 失败 (非 own) | 403 | ✅ |
| T-P15 | regular admin `delete_training_job` 失败 (非 own) | 403 | ✅ |
| T-P16 | regular admin `delete_model` 失败 (别人的 model) | 403 | ✅ |
| T-P17 | regular admin `deactivate_model` 失败 (孤儿 model) | 403 | ✅ |
| T-P18 | owner 仍可正常 cancel/view log (确保不影响正常路径) | 200 | ✅ |

**总计 17 项全通过** + 4 项 v3.3.4 既有测试 + 28 项 cross-user-isolation 测试 + 10 项 team-cache 测试 + 6 项 datasets 测试 = **78 项权限/隔离相关测试全部通过**。

---

## 五、修改文件清单

### 5.1 后端代码 (11 个文件)

| 文件 | 修复点 |
|------|--------|
| `app/tasks/api/dataset.py` | `list_datasets` `is_admin()` → `is_super_admin()` + `my_access` 标注收紧 |
| `app/tasks/api/training/jobs.py` | `list_training_jobs` 收紧 + `delete_training_job` 改用 `assert_can_access_training_job` |
| `app/tasks/api/model/query.py` | `list_models` 收紧 + `get_model_detail`/`list_active_models` 改用 `is_super_admin()` |
| `app/tasks/api/model/deletion.py` | `delete_model`/`batch_delete_models` 孤儿旁路收紧 |
| `app/tasks/api/model/activation.py` | `deactivate_model` 孤儿旁路收紧 |
| `app/tasks/api/training/progress.py` | `_assert_can_access_task_id` 改用 `is_super_admin()` |
| `app/tasks/api/training/log.py` | `_assert_can_access_job` 改用 `is_super_admin()` |
| `app/tasks/api/segmentation/progress.py` | 2 处改用 `is_super_admin()` |
| `app/tasks/api/detection/progress.py` | 3 处改用 `is_super_admin()` |
| `app/annotation/api/annotation.py` | `recent_annotations` 改用 `is_super_admin()` |
| `app/tasks/service/permission_service.py` | 新增 `assert_can_access_training_job` / `assert_can_access_model` |

### 5.2 新增测试 (1 个文件)

| 文件 | 描述 |
|------|------|
| `tests/test_permission_v334_patch.py` | 17 项 PATCH 回归测试 |

### 5.3 文档 (本报告)

| 文件 | 描述 |
|------|------|
| `docs/task-summaries/009-task-summary.md` | 本报告 (PATCH 全面修复总结) |

---

## 六、修复前后对比

### 6.1 修复前 (越权场景)

| 角色 | 操作 | 修复前结果 | 修复后结果 |
|------|------|----------|----------|
| regular admin | GET `/api/training/jobs` | 看到全部用户训练任务 | 仅自己 + 团队共享 |
| regular admin | GET `/api/models` | 看到全部用户模型 | 仅自己 + 团队共享 |
| regular admin | GET `/api/annotations/recent` | 看到全部用户活动流 | 仅自己 + 团队共享 |
| regular admin | POST `/api/training/jobs/{id}/cancel` (别人 job) | 200 (越权) | 403 |
| regular admin | DELETE `/api/models/{id}` (别人 model) | 200 (越权) | 403 |
| regular admin | DELETE `/api/models/{id}` (孤儿 model) | 200 (越权) | 403 |
| regular admin | GET `/api/training/progress/{task_id}` (别人) | 200 (越权) | 403 |

### 6.2 保留行为 (确保不破坏现有功能)

| 角色 | 操作 | 结果 |
|------|------|------|
| super_admin | 任何数据级操作 | 200 (平台级审计) |
| owner | 自己的资源 | 200 (正常路径) |
| team member (manager/editor) | 团队共享资源 | 200 (按团队角色) |
| team member (viewer) | 团队共享资源 (读) | 200 |
| team member (viewer) | 团队共享资源 (写) | 403 (`require_write=True` 拦截) |
| owner 跨 team 操作自己创建的 dataset | list 接口 | 显示为 personal (不受 team_id 影响) |

---

## 七、变更影响范围

### 7.1 受影响的前端场景

- `regular admin` 用户登录后, 数据集/训练/模型/标注活动列表自动按个人+团队共享过滤
- 前端无需改动, 后端 API 响应结构不变 (仅 `items` 列表内容按可见性过滤)

### 7.2 不受影响的场景

- `super_admin` 视角完全保留
- 用户个人资源访问不受影响
- 团队成员对团队共享资源的访问不受影响
- 所有系统级管理 API (用户管理/审计查询) 仍走 `is_admin()` (按设计)

### 7.3 数据库迁移

**无需迁移**: 本次 PATCH 仅修改应用层权限校验逻辑, 不修改表结构/索引。

---

## 八、待提交内容

```bash
# 1. 后端代码 (11 个文件修改)
git add app/tasks/api/dataset.py
git add app/tasks/api/training/jobs.py
git add app/tasks/api/training/progress.py
git add app/tasks/api/training/log.py
git add app/tasks/api/model/query.py
git add app/tasks/api/model/deletion.py
git add app/tasks/api/model/activation.py
git add app/tasks/api/segmentation/progress.py
git add app/tasks/api/detection/progress.py
git add app/annotation/api/annotation.py
git add app/tasks/service/permission_service.py

# 2. 新增测试
git add tests/test_permission_v334_patch.py

# 3. 文档
git add docs/task-summaries/009-task-summary.md

# 4. 提交 (按团队约定, 建议拆为 2 个 commit)
# commit 1: fix(permission) - 代码修复
# commit 2: test(permission) - 测试 + 文档
```

---

## 九、回归测试结果汇总

| 测试套件 | 测试数 | 通过 | 失败 |
|---------|-------|------|------|
| test_permission_v334_audit.py | 14 | 14 | 0 |
| test_permission_v334_patch.py (新增) | 17 | 17 | 0 |
| test_permission_v334_token_revocation.py | 3 | 3 | 0 |
| test_cross_user_isolation.py | 28 | 28 | 0 |
| test_team_cache.py | 10 | 10 | 0 |
| test_datasets.py | 6 | 6 | 0 |
| **合计** | **78** | **78** | **0** |

耗时 134.58s (≈2:14), 全部 PASSED, 无回归。

---

## 十、待跟进事项

1. **提交前确认**: 关联 P0-1 的 `list_datasets` 修复 (008 报告) + P0-2~P0-6 + P1 的 PATCH 修复 + 17 项测试, 建议合并为 1 个 commit `fix(permission): v3.3.4-PATCH 全面修复 list 接口 + 单条操作 admin 旁路 (11 个后端文件 + 1 个测试)`
2. **前端验证**: regular admin 登录后, 数据集/训练/模型/标注活动列表的可见性应符合预期 (可手动测试)
3. **后续 PR 评审**: 评审重点关注:
   - team 共享过滤的 SQL 性能 (子查询是否需要加索引)
   - 孤儿 model 的归属 (是否应改为强制要求 `dataset_id` 非空, 而非通过 `super_admin` 旁路)
4. **v3.3.4 全量收尾**: 008 + 009 报告合计 22 项测试 + 14+ 个文件修改, 应作为 v3.3.4 完整安全加固合并到 main 分支
