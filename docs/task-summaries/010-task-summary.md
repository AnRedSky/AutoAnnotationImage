# 任务 010 总结报告 - v3.3.5-PERMISSION-REWRITE 全面权限架构重写 + v3.3.6-STATS-ISOLATION 统计越权修复

> **报告日期**: 2026-08-05
> **阶段**: v3.3.5 权限架构重写 + v3.3.6 统计越权修复
> **状态**: 代码完成, 待提交推送
> **核心交付**: 严格数据隔离 + 孤儿 model 强制约束 + 越权审计日志 + 统计接口严格最小权限
> **前置报告**: 008/009 (v3.3.4 + v3.3.4-PATCH)

---

## 一、问题背景

v3.3.4-PATCH 已修复 `admin` 角色的越权问题, 但 `super_admin` 仍可在数据级接口旁路所有权限校验:
- `list_datasets` / `list_training_jobs` / `list_models` 等 list 接口: `is_super_admin()` 直接旁路
- `delete_model` / `deactivate_model` 等单条操作: 孤儿 model 仍由 super_admin 旁路
- 训练进度/日志接口: super_admin 仍可查看全平台
- `User.can_access_dataset` 同步方法: 仍用 `is_admin()` 旁路 (重大隐患)

**核心矛盾**: 现有架构将 super_admin 视为"全平台数据访问者", 与最小权限原则冲突。

---

## 二、新权限模型 (3 层架构)

### 2.1 层级划分

| 层级 | 范围 | 准入 | 典型操作 |
|------|------|------|---------|
| **L1 系统层** | 平台级管理 | `super_admin` | 用户管理/审计/系统统计/租户/系统配置 |
| **L2 团队层** | 团队资源 | 团队成员 | 团队 CRUD / 成员管理 / 团队数据集共享 / 团队活动 |
| **L3 数据层** | 业务数据 | owner / team 成员 | 数据集/图片/标注/训练/模型/导出 |

### 2.2 super_admin 权限边界 (v3.3.5 新增)

| 层级 | super_admin 是否旁路 | 理由 |
|------|---------------------|------|
| L1 系统层 | ✅ 旁路 | 平台运维/审计的合理需求 |
| L2 团队层 | ⚠️ 部分旁路 | 仅可看 team activities (审计), 不能修改 team |
| L3 数据层 | ❌ 不旁路 | 数据级访问严格受 owner/team 约束, 与普通用户一致 |

### 2.3 角色权限矩阵

| 角色 | L1 系统层 | L2 团队层 | L3 数据层 |
|------|----------|----------|----------|
| `super_admin` | ✅ 全通 | ✅ 只读活动 Feed | ❌ 受 owner/team 约束 |
| `admin` (regular) | ⚠️ 部分 (用户列表只读 + 审计) | ❌ 受 team 成员关系约束 | ❌ 受 owner/team 约束 |
| `annotator` | ❌ | ❌ 受 team 成员关系约束 | ❌ 受 owner/team 约束 |
| `viewer` | ❌ | ❌ 受 team 成员关系约束 | ❌ 受 owner/team 约束 |

### 2.4 越权处理

- 拒绝: 返回 **HTTP 403** + 中文错误消息
- 审计: 记录到 `audit_log`, `event_type = "permission_denied"`
- 字段: user_id / resource_type / resource_id / detail (含 reason, endpoint) / ip_address

---

## 三、修复清单

### 3.1 核心服务层 (permission_service.py)

| 修复点 | 旧逻辑 | 新逻辑 |
|--------|--------|--------|
| `assert_can_access_dataset` | super_admin 直接 return | 移除 super_admin 旁路, 走 owner/team |
| `assert_can_access_training_job` | super_admin 直接 return | 移除 super_admin 旁路 |
| `assert_can_access_model` | super_admin 直接 return | 移除 super_admin 旁路 |
| 新增 `log_permission_denied` | - | 统一记录 403 越权 |

### 3.2 User 模型 (user.py)

| 修复点 | 旧逻辑 | 新逻辑 |
|--------|--------|--------|
| `can_access_dataset` (同步) | `is_admin()` 旁路 | 仅检查 owner, 注释引导调用异步 `assert_can_access_dataset` |

### 3.3 数据级 list 接口 (移除 super_admin 旁路)

| 文件 | 函数 | 旧逻辑 | 新逻辑 |
|------|------|--------|--------|
| `dataset.py` | `list_datasets` | super_admin 看全部 | 仅 owner + 团队共享 |
| `model/query.py` | `list_models` / `get_model_detail` / `list_active_models` | super_admin 旁路 | 走 `assert_can_access_model` |
| `training/jobs.py` | `list_training_jobs` | super_admin 看全部 | 仅 owner + 团队共享 |
| `annotation.py` | `recent_annotations` | super_admin 看全平台 | 仅 owner + 团队共享 |

### 3.4 数据级单条操作

| 文件 | 函数 | 旧逻辑 | 新逻辑 |
|------|------|--------|--------|
| `training/jobs.py` | `cancel_training_job` / `pause_training_job` / `delete_training_job` / `get_training_error` | super_admin 旁路 | `assert_can_access_training_job` |
| `model/deletion.py` | `delete_model` / `batch_delete_models` | super_admin 旁路孤儿 model | 拒绝孤儿 model (要求 dataset_id 非空) |
| `model/activation.py` | `deactivate_model` | super_admin 旁路孤儿 model | 拒绝孤儿 model |
| `training/progress.py` | `_assert_can_access_task_id` | super_admin 旁路 | `assert_can_access_training_job` |
| `training/log.py` | `_assert_can_access_job` | super_admin 旁路 | `assert_can_access_training_job` |
| `detection/progress.py` | 3 处 | super_admin 旁路 | 走训练任务权限函数 |
| `segmentation/progress.py` | 2 处 | super_admin 旁路 | 走训练任务权限函数 |

### 3.5 孤儿 model 治理 (强制 dataset_id 非空)

| 步骤 | 描述 |
|------|------|
| 1. 数据库迁移 | `ModelVersion.dataset_id` 改为 NOT NULL (兜底: 清理前先 SELECT 看孤儿数) |
| 2. 应用层 | `delete_model` / `batch_delete_models` / `deactivate_model` 拒绝孤儿 model |
| 3. 清理脚本 | 一次性迁移脚本: 关联到默认 dataset 或删除 (按业务需求决策) |

### 3.6 系统级接口 (保留 super_admin 旁路)

| 文件 | 函数 | 逻辑 |
|------|------|------|
| `audit.py` | `list_audit_logs` | `is_admin()` (合规审计场景) |
| `stats.py` | `stats_overview` | `is_admin()` (平台统计) |
| `user.py` | `list_users` / `change_role` / `reset_password` | `is_admin()` (用户管理) |
| `team.py` | `list_teams` (跨团队列表) | `is_admin()` |
| `team.py` | `list_team_activities` (单团队) | super_admin 旁路保留 (审计) |

### 3.7 新增审计事件类型

`audit_log` 新增事件类型:
- `permission_denied` (越权 403 拒绝)

---

## 四、回归测试

新建 `tests/test_permission_v335_rewrite.py`, 覆盖:
- **T-R01~R05**: super_admin 列表接口仅看 own + 团队共享
- **T-R06~R10**: super_admin 单条操作仅 owner/team 通过
- **T-R11~R15**: 越权尝试均记录 `permission_denied` 审计
- **T-R16~R18**: 孤儿 model 操作 (delete/activate/list) 全部 403
- **T-R19~R20**: 系统级接口 (audit/stats) super_admin 仍可访问
- **T-R21~R22**: 旧 `is_admin()` 用法回归 (确保不影响)

**总计 22 项 v3.3.5 新增 + 78 项既有测试 = 100 项权限/隔离测试**

---

## 五、修改文件清单

### 5.1 后端代码 (12 个文件)

| 文件 | 修复点 |
|------|--------|
| `app/tasks/service/permission_service.py` | 移除 super_admin 旁路 + 新增 log_permission_denied |
| `app/tasks/service/audit_service.py` | 新增 `permission_denied` helper |
| `app/tasks/model/audit_log.py` | 扩展 `AUDIT_EVENT_TYPES` |
| `app/admin/model/user.py` | `can_access_dataset` 移除 is_admin 旁路 |
| `app/tasks/api/dataset.py` | `list_datasets` 移除 super_admin 旁路 |
| `app/tasks/api/training/jobs.py` | list + delete 移除 super_admin 旁路 |
| `app/tasks/api/model/query.py` | list/detail/active 移除 super_admin 旁路 |
| `app/tasks/api/model/deletion.py` | 拒绝孤儿 model |
| `app/tasks/api/model/activation.py` | 拒绝孤儿 model |
| `app/tasks/api/training/progress.py` | 移除 super_admin 旁路 (REST + SSE) |
| `app/tasks/api/training/log.py` | 移除 super_admin 旁路 (get + append) |
| `app/tasks/api/detection/progress.py` | 移除 super_admin 旁路 (3 处) |
| `app/tasks/api/segmentation/progress.py` | 移除 super_admin 旁路 (3 处) |
| `app/annotation/api/annotation.py` | `recent_annotations` 移除 super_admin 旁路 |
| `app/tasks/model/model_version.py` | 注释标注 dataset_id 收紧为 NOT NULL (配合 migration) |

### 5.2 新增测试 (1 个文件)

| 文件 | 描述 |
|------|------|
| `tests/test_permission_v335_rewrite.py` | 22 项 REWRITE 回归测试 |

### 5.3 数据库迁移 (1 个文件)

| 文件 | 描述 |
|------|------|
| `migrations/enforce_model_dataset_notnull.py` | 强制 ModelVersion.dataset_id 非空 (含孤儿 model 清理) |

### 5.4 文档 (本报告)

| 文件 | 描述 |
|------|------|
| `docs/task-summaries/010-task-summary.md` | 本报告 (PERMISSION-REWRITE) |

---

## 六、修复前后对比

### 6.1 修复前 (super_admin 越权)

| 角色 | 操作 | 修复前结果 | 修复后结果 |
|------|------|----------|----------|
| super_admin | GET `/api/datasets` | 看到全部用户 dataset | 仅 own + 团队共享 |
| super_admin | GET `/api/training/jobs` | 看到全部训练任务 | 仅 own + 团队共享 |
| super_admin | GET `/api/models` | 看到全部 model | 仅 own + 团队共享 |
| super_admin | DELETE `/api/models/{id}` (别人) | 200 (越权) | 403 + 审计 |
| super_admin | DELETE `/api/models/{id}` (孤儿) | 200 (越权) | 403 + 审计 |
| super_admin | GET `/api/annotations/recent` | 看全平台 | 仅 own + 团队共享 |
| super_admin | GET `/api/training/progress/{id}` (别人) | 200 (越权) | 403 + 审计 |
| super_admin | GET `/api/audit-logs` | 200 (系统层, 合理) | 200 (保持) |
| super_admin | GET `/api/users/` | 200 (系统层, 合理) | 200 (保持) |

### 6.2 系统级接口 (super_admin 仍可访问)

| 端点 | 角色要求 | 理由 |
|------|---------|------|
| `/api/audit-logs` | admin+ | 平台合规审计 |
| `/api/users/` | admin+ | 用户管理 |
| `/api/stats/overview` | admin+ | 平台统计 |
| `/api/teams/` (跨团队列表) | admin+ | 平台管理 |
| `/api/teams/{id}/activities` | 团队成员 + super_admin | 团队活动 feed (super_admin 审计) |

---

## 七、变更影响范围

### 7.1 受影响的前端场景

- super_admin 登录后, 数据集/训练/模型/标注活动列表自动按个人+团队共享过滤
- super_admin 操作他人资源时返回 403, 前端需显示权限错误提示
- 孤儿 model 不再出现 (数据库约束 + 应用层拒绝)

### 7.2 不受影响的场景

- 用户管理界面 (super_admin 仍可访问)
- 审计日志 (super_admin 仍可访问)
- 系统统计 (super_admin 仍可访问)
- 用户自己的资源
- 团队成员对自己团队的资源

### 7.3 数据库迁移

- **新增**: `ModelVersion.dataset_id` 改为 NOT NULL
- **风险**: 存在孤儿 model 时迁移失败, 需先清理 (提供清理脚本)
- **回滚**: 提供 downgrade 脚本

---

## 八、v3.3.6-STATS-ISOLATION 统计接口越权修复

### 8.1 背景

用户反馈"数据统计还存在越权问题, 不限于仪表盘的统计数据"。经全面排查, 发现 v3.3.5 虽修复了 list/单条操作接口的 super_admin 旁路, 但**统计/聚合接口**仍存在以下越权:

| 接口 | 越权点 | 风险等级 |
|------|--------|---------|
| `GET /api/stats/overview` | `is_admin()` 旁路, 任何 admin 看全平台 datasets/images/model_versions/training_jobs 数量 | P0 |
| `GET /api/stats/overview` (admin 分支) | `model_versions` 直接 `func.count(ModelVersion.id)` 全平台统计 | P0 |
| `GET /api/stats/annotator-efficiency` | `is_admin()` 旁路, 任何 admin 看全平台所有用户 username + 标注量 | **P0 严重隐私泄露** |
| `DELETE /api/teams/datasets/{id}/share` | `is_admin()` 旁路, regular admin 可越权取消别人的共享 (横向越权) | P1 |
| `StatsService.global_overview` | 返回全平台聚合, 调用方如未做隔离会泄露全平台数据 | P0 |
| `union_all` 不去重 (stats/model list) | owner 同时是 team member 时, 计数翻倍 | 数据准确性 |

### 8.2 修复清单 (4 个后端文件 + 1 个测试文件)

| 文件 | 修复点 |
|------|--------|
| `backend/app/admin/api/stats.py` | (1) /overview 移除 admin 旁路, 任何角色按可见数据集过滤; (2) 修复 union_all 不去重导致的计数翻倍; (3) /annotator-efficiency 移除 admin 旁路, 仅返回 self |
| `backend/app/admin/api/team.py` | (4) unshare_dataset 收紧: regular admin 改为仅 super_admin 可越权 |
| `backend/app/admin/service/stats_service.py` | (5) `global_overview` 标记 DEPRECATED, 触发 DeprecationWarning, 防止误用 |
| `backend/app/tasks/api/model/query.py` | (6) union_all → union 修复 owner+member 计数翻倍 |
| `backend/tests/test_permission_v336_stats_isolation.py` | (7) 11 项回归测试 (T-S01~T-S10 + StatsService DEPRECATED 验证) |

### 8.3 关键修复点详解

#### 8.3.1 /api/stats/overview 移除 admin 旁路

**修复前**:
```python
if current_user.is_admin():
    # 管理员: 保留全局视角
    overview = await StatsService.global_overview(db)
    return {"datasets": overview["datasets"]["total"], ...}
```

**修复后**:
```python
# v3.3.6: 移除 admin/super_admin 旁路, 任何角色均按可见数据集过滤
own_ds_ids_subq = select(Dataset.id).where(Dataset.owner_id == current_user.id)
team_ds_ids_subq = select(Dataset.id).join(TeamMember, ...)
visible_ds_ids_stmt = own_ds_ids_subq.union(team_ds_ids_subq)  # union 去重
```

#### 8.3.2 /api/stats/annotator-efficiency 移除 admin 旁路

**修复前** (admin 分支返回全平台 Top 10):
```python
if current_user.is_admin():
    # 管理员: 保留全局视角
    stmt = select(AnnotationLog.user_id, User.username, ...).join(User, ...)  # 全平台
```

**修复后** (任何角色仅 self):
```python
# v3.3.6: 任何角色 (含 super_admin) 均按 self only 返回
stmt = select(...).where(AnnotationLog.user_id == current_user.id)
```

#### 8.3.3 unshare_dataset 权限收紧

**修复前**:
```python
if not current_user.is_admin() and dataset.owner_id != current_user.id:
    raise HTTPException(403, "...")
```

**修复后** (regular admin 不可越权):
```python
if not current_user.is_super_admin() and dataset.owner_id != current_user.id:
    raise HTTPException(403, "无权限取消共享: 仅数据集原始共享者或超级管理员可操作")
```

#### 8.3.4 union_all → union 修复计数翻倍

**问题**: owner 同时是 team member 时, 自己的 dataset 会在两个子查询中重复, `union_all` 不会去重, 导致计数翻倍。

**修复**:
```python
visible_ds_ids_stmt = own_ds_ids_subq.union(team_ds_ids_subq)  # union (去重)
```

#### 8.3.5 StatsService.global_overview 标记 DEPRECATED

**风险**: 任何调用方未做用户隔离即可获得全平台业务数据, 严重越权。

**修复**: 标记为已废弃, 调用触发 `DeprecationWarning` + `logger.warning`, 引导使用 `/api/stats/overview` (按用户隔离) 或 `/api/teams/{id}/stats` (按团队隔离)。

### 8.4 测试矩阵 (11 项)

| 测试 ID | 场景 | 期望 |
|---------|------|------|
| T-S01 | super_admin /stats/overview | 不包含其他用户的 dataset 数 |
| T-S02 | super_admin /stats/overview model_versions | 仅统计可见数据集 |
| T-S03 | regular admin /stats/overview | 不包含其他用户的 dataset 数 |
| T-S04 | alice /stats/overview | 仅返回自己创建的数据集相关统计 |
| T-S05 | super_admin /stats/annotator-efficiency | 仅返回自己 (严重隐私保护) |
| T-S06 | regular admin /stats/annotator-efficiency | 仅返回自己 |
| T-S07 | alice /stats/annotator-efficiency | 仅返回自己 (5 条标注) |
| T-S08 | regular admin 取消他人共享 | 403 (v3.3.6 收紧) |
| T-S09 | super_admin 取消他人共享 | 200 (平台代管场景) |
| T-S10 | owner 取消自己的共享 | 200 (正常路径) |
| - | StatsService.global_overview 源码 | 含 DEPRECATED 标记 |

**全部 11 项通过** ✅

### 8.5 业务影响

- **平台运营**: admin 失去全平台数据视角, 需通过 `/api/users` (admin only) + `/api/teams/{id}/stats` 自行聚合
- **隐私保护**: 不再可能通过 dashboard 枚举全平台用户标注量, 防止竞品分析团队规模
- **数据准确性**: 修复 owner+member 计数翻倍, 统计指标更可信
- **横向越权**: regular admin 无法再破坏别人的协作关系 (取消共享)

### 8.6 前端影响

- Dashboard.vue 直接调用 `statsApi.overview()` 和 `statsApi.annotatorEfficiency()`, 无需修改
- 字段名未变 (items, username, annotation_count 等), 自动适配
- admin 用户的「标注员效率排行」图表现在只显示自己一行 (预期行为)

---

## 九、待跟进事项

1. **前端验证**: super_admin 登录后, 数据集/训练/模型列表的可见性应符合预期
2. **孤儿 model 清理**: 部署前先执行清理脚本, 确认无孤儿数据后再做 NOT NULL 迁移
3. **PR 评审**: 重点关注 403 审计日志的写入性能 (permission_denied 事件可能高频)
4. **文档同步**: 更新用户手册/部署文档, 告知 super_admin 权限边界变化
5. **Dashboard UI 优化**: 标注员效率排行图对 admin 现在只显示自己, 可加提示"完整团队效率请前往 /api/teams/{id}/stats"
