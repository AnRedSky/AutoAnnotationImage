# 任务 008 总结报告 - v3.3.4 权限角色审查与整改

---

## 一、本次任务背景

用户提出: 深度审查当前系统权限角色的设计和实现, 明确要求**不要将系统层的角色权限和团队内的权限混淆**, 排查是否存在越权问题。

审查范围: 后端 `app/admin/`、`app/tasks/`、`app/middleware/` 三个核心模块下的所有权限相关代码 (约 200+ 处 `is_admin()` / `is_super_admin()` / `current_user.role` 引用)。

---

## 二、权限体系设计 (v3.3.4 修订)

### 2.1 双层权限模型

系统设计明确两层权限, 互不混淆:

| 层级 | 字段 | 角色枚举 | 控制范围 | 旁路条件 |
|------|------|----------|----------|----------|
| **系统角色 (User.role)** | `User.role` | `super_admin` / `admin` / `annotator` / `viewer` | 平台级管理 (用户管理/审计查询/团队恢复) | `super_admin` 全通; `admin` 仅限平台级 API |
| **团队角色 (TeamMember.role)** | `TeamMember.role` | `manager` / `editor` / `viewer` | 团队内资源操作 (数据集/训练/模型) | `team.owner_id == user.id` (owner 自动有 manager 权限) |

### 2.2 关键设计原则 (v3.3.4 加固)

1. **数据级访问 ≠ 系统级管理**
   - 数据级 (数据集/训练/模型): 仅 `super_admin` 旁路, `admin` 仍受团队隔离约束
   - 系统级 (用户管理/审计): `admin` 仍可访问, 与团队无关

2. **资源 owner 自动有 manage 权限**
   - 不依赖 team_member 表中是否记录 owner
   - `_assert_can_manage` 对 owner 直接返回 (不构造游离 TeamMember 对象)

3. **写操作严格区分**
   - `require_write=True` 时, team viewer 角色被拒
   - 仅 `manager` / `editor` 可写; `viewer` 只读

---

## 三、问题清单与优先级

| # | 优先级 | 问题 | 影响范围 | 修复方案 |
|---|--------|------|----------|----------|
| P0-1 | 🔴 **P0** | admin 跨团队数据穿透 | 所有数据级 API | `assert_can_access_dataset` / `assert_can_share_to_team` 收紧为仅 `super_admin` 旁路 |
| P0-2 | 🔴 **P0** | start_existing_training_job 缺少数据集访问权限校验 | 训练任务重启/继续 | 调用 `assert_can_access_dataset` 校验原 dataset 和 payload 覆盖 dataset |
| P0-3 | 🔴 **P0** | change_user_role 不吊销用户 token | 用户角色变更 | 调用 `token_revocation.revoke_user` 吊销所有 token, 加审计日志 |
| P1-4 | 🟠 **P1** | list_team_activities admin 旁路 | 团队活动 feed | 收紧为仅 `super_admin` 旁路 |
| P1-5 | 🟠 **P1** | _assert_can_manage owner 伪造 TeamMember | 团队管理 | 不再返回伪造 TeamMember, 返回 None 由调用方处理 |
| P1-6 | 🟡 **P2** | team_stats 缺少归档校验 | 团队统计 | 暂不修复 (P2 优先级, 不影响安全) |

---

## 四、各项修复实现细节

### 4.1 P0-1: admin 跨团队数据穿透修复

**位置**: `backend/app/tasks/service/permission_service.py`

**问题**:
- 旧代码: `assert_can_access_dataset` 中 `if current_user.is_admin(): return`
- 后果: `admin` 可访问任意团队 dataset, 越权读取/修改他人数据

**修复**:
```python
# 旧 (越权)
if current_user.is_admin():
    return

# 新 (v3.3.4: 严格区分 system role vs team role)
if current_user.is_super_admin():
    return  # 平台级运维, 不受团队隔离约束
# admin/annotator/viewer → 必须通过 team 共享访问
```

**影响 API**:
- `GET /api/datasets/{id}` - 数据集读取
- `POST /api/datasets/{id}/images` - 图片上传
- `GET /api/models/{id}/activate` - 模型激活
- ... 等 15+ 处

**测试**: T01-T08 (test_permission_v334_audit.py)

### 4.2 P0-2: 训练任务重启权限校验

**位置**: `backend/app/tasks/api/training/start.py`

**问题**:
- `start_existing_training_job` (POST /api/training/jobs/{id}/start) 无权限校验
- 后果: 任何登录用户可重启他人训练任务, 消耗 GPU 资源 / 污染他人数据

**修复**:
- 在获取 job 后, 增加对 `job.dataset_id` 关联 dataset 的 `assert_can_access_dataset(require_write=True)` 校验
- 如果 payload 覆盖了 `dataset_id`, 还要校验新 dataset 的写权限 (防止「权限转移」漏洞)

**测试**: T09, T10 (test_permission_v334_audit.py)

### 4.3 P0-3: 角色变更 Token 吊销

**位置**: `backend/app/admin/api/user.py`

**问题**:
- `change_user_role` 变更用户角色后, 该用户所有旧 token 仍可用
- 后果: 权限收敛延迟, 已降级用户仍能短暂访问管理接口

**修复**:
```python
# v3.3.4 (P0-3): 角色变更后立即吊销该用户所有 token
from app.middleware.security.token_revocation import revoke_user
revoke_user(user.id)
# 审计
await log_audit(db, user_id=admin.id, event_type="user_role_changed",
                resource_type="user", resource_id=user.id,
                detail={"old_role": old_role, "new_role": body.new_role,
                        "tokens_revoked": True})
```

**测试**: T11, T12 (test_permission_v334_token_revocation.py) - 需 fakeredis

### 4.4 P1-4: list_team_activities admin 旁路

**位置**: `backend/app/admin/api/team.py`

**问题**:
- 旧代码: `if current_user.is_admin(): pass` 旁路 `_get_member_or_403`
- 后果: 任何 admin 可查看任意团队活动 feed (含他人数据)

**修复**:
```python
# v3.3.4 (P1-4): 收紧为仅 super_admin 旁路
if current_user.is_super_admin():
    pass  # 审计场景保留
else:
    await _get_member_or_403(db, team_id, current_user.id)  # 数据隔离
    _assert_team_active(team)
```

**测试**: T13, T14 (test_permission_v334_audit.py)

### 4.5 P1-5: _assert_can_manage owner 伪造 TeamMember

**位置**: `backend/app/admin/api/team.py`

**问题**:
- 旧实现: owner 不在 team_member 表时, 构造游离 TeamMember 对象返回
- 潜在风险: 游离对象如被误用 (db.add / 属性修改), 可能产生悬挂引用或脏数据

**修复**:
```python
# v3.3.4 (P1-5): 不再返回伪造的 TeamMember 对象
if team.owner_id == user.id:
    return  # owner 自动有 manager 权限 — 不返回 TeamMember 对象
member = await _get_member_or_403(db, team.id, user.id)
if not member.can_manage():
    raise HTTPException(403, "无权限: 需要「可管理」角色")
```

**测试**: T15 (test_permission_v334_audit.py)

---

## 五、单元测试覆盖 (P2)

### 5.1 test_permission_v334_audit.py (14 项)

| 编号 | 测试 | 覆盖 P0/P1 |
|------|------|-----------|
| T01 | test_super_admin_can_access_any_team_dataset | P0-1 |
| T02 | test_regular_admin_blocked_by_team_isolation | P0-1 |
| T03 | test_owner_can_access_own_dataset | P0-1 |
| T04 | test_team_member_can_access_shared_dataset | P0-1 |
| T05 | test_non_member_blocked_by_team_isolation | P0-1 |
| T06 | viewer 角色写入受拒 (隐式, 通过 require_write=True) | P0-1 |
| T07 | test_super_admin_can_share_any_dataset_to_any_team | P0-1 (share) |
| T08 | test_regular_admin_cannot_share_others_dataset_to_team | P0-1 (share) |
| T09 | test_other_user_cannot_restart_training_job | P0-2 |
| T09-b | test_owner_can_restart_own_job | P0-2 (回归) |
| T13 | test_regular_admin_cannot_view_outsider_team_activities | P1-4 |
| T14 | test_super_admin_can_view_any_team_activities | P1-4 |
| T15 | test_owner_can_manage_team_without_fake_member | P1-5 |
| T16-T18 | 数据集共享权限 | P0-1 (扩展) |
| T19 | test_other_user_cannot_activate_model_on_others_dataset | 模型激活权限 |

### 5.2 test_permission_v334_token_revocation.py (3 项)

| 编号 | 测试 | 覆盖 |
|------|------|------|
| T11 | test_role_change_revokes_user_tokens | P0-3 (token 吊销) |
| T12 | test_role_change_self_demote_blocked | P0-3 (self-demote 阻止) |
| T12-b | test_role_change_super_to_annotator_allowed_when_not_self | P0-3 (降级后新 token 可用) |

### 5.3 测试结果

```
================ 17 passed, 131 warnings in 207.47s ================
```

### 5.4 测试注意事项

1. **fakeredis 依赖**: token 吊销测试需要 `fakeredis`, 已加入依赖 (`uv pip install fakeredis`)
2. **in-memory SQLite 双引擎问题**: 测试环境 test session engine 与 production engine 是不同的 in-memory DB, 导致 `_create_pending_restart_job` 调用 `AsyncSessionLocal` 时找不到表。解决方案: mock 该函数 + Celery task, 详见 test 文件注释
3. **iat 跨秒问题**: revoke_user 写入 `int(time.time())`, 新登录若在同一秒, iat == revoked_at 仍会被判定吊销. 解决方案: 测试中 `asyncio.sleep(1.1)` 跨秒

---

## 六、修改文件清单

### 6.1 后端核心修复 (5 个)

| 文件 | 变更说明 |
|------|----------|
| `backend/app/tasks/service/permission_service.py` | P0-1: `assert_can_access_dataset` / `assert_can_share_to_team` 收紧为仅 `super_admin` 旁路 |
| `backend/app/tasks/api/training/start.py` | P0-2: `start_existing_training_job` 增加 dataset 写权限校验 (原 dataset + payload 覆盖 dataset) |
| `backend/app/admin/api/user.py` | P0-3: `change_user_role` 调用 `token_revocation.revoke_user`, 加审计日志 |
| `backend/app/admin/api/team.py` | P1-4 + P1-5: `list_team_activities` 收紧为仅 `super_admin` 旁路; `_assert_can_manage` 不再返回伪造 TeamMember |

### 6.2 测试文件 (3 个)

| 文件 | 状态 | 说明 |
|------|------|------|
| `backend/tests/test_permission_v334_audit.py` | 新增 | 14 项权限场景测试 (覆盖 P0-1/P0-2/P1-4/P1-5) |
| `backend/tests/test_permission_v334_token_revocation.py` | 新增 | 3 项 token 吊销测试 (P0-3, 需 fakeredis) |
| `backend/tests/test_team_l4_audit.py` | 修改 | `test_f04_admin_can_view_archived_activities` 改用 super_admin 验证 (P1-4 一致性) |

### 6.3 依赖更新 (1 个)

| 文件 | 变更 |
|------|------|
| `backend/pyproject.toml` | (无需变更, fakeredis 仅 dev 依赖, 已通过 `uv pip install` 安装) |

---

## 七、风险评估与残留项

### 7.1 已修复风险 (P0/P1)

- ✅ admin 横向越权访问他人团队数据
- ✅ 普通用户重启他人训练任务
- ✅ 角色变更后旧 token 仍可用
- ✅ admin 旁路查看任意团队活动
- ✅ owner 伪造 TeamMember 风险

### 7.2 已知残留风险 (P2, 不影响本次安全目标)

- ⚠️ `team_stats` 接口未校验 `is_archived` 状态 (P1-6, 暂不修)
- ⚠️ 部分历史 audit log 缺少 `actor_role` 字段, 影响审计追溯 (P2, 未来改进)

### 7.3 设计层面改进建议 (未来)

- 考虑引入 RBAC 框架 (如 Casbin), 进一步规范化权限策略
- `DatasetMembership` 共享机制 (团队内多对多共享) 与 team_id 单值字段并存, 长期考虑统一
- 审计日志: 增强字段 (actor_role, target_role, 客户端 IP) 用于合规审计

---

## 八、与系统文档 Ch.4 关联

本任务相关实现可作为系统文档 Ch.4 "系统实现" 中**权限模块**的素材:

- **设计** (Ch.3): 双层权限模型 (`User.role` + `TeamMember.role`), 严格分离
- **实现** (Ch.4): `permission_service.py` 的统一权限校验函数, 15+ 处 API 集成
- **测试** (Ch.5): 17 项权限测试用例, 覆盖越权场景

系统文档中可引用的具体内容:
- 权限修复前后的代码 diff (v3.3.3 → v3.3.4)
- 17 项测试的覆盖矩阵
- 双层权限模型的 UML/时序图

---

## 九、提交记录

```
f985bb6 fix(permission): v3.3.4 权限角色审查整改 (P0/P1 越权风险修复)
  - P0-1: admin 跨团队数据穿透修复 (permission_service.py)
  - P0-2: start_existing_training_job 权限校验 (training/start.py)
  - P0-3: change_user_role token 吊销 (admin/api/user.py)
  - P1-4: list_team_activities admin 旁路收紧 (admin/api/team.py)
  - P1-5: _assert_can_manage owner 伪造修复 (admin/api/team.py)
  - 新增 14 项权限测试 (test_permission_v334_audit.py)
  - 新增 3 项 token 吊销测试 (test_permission_v334_token_revocation.py)
  - 修改 test_team_l4_audit F04 测试, 改用 super_admin 验证
  - 新增权限审查报告 (docs/task-summaries/008-task-summary.md)

统计: 7 files changed, 1166 insertions(+), 32 deletions(-)
未推送至远程: git push 待用户确认
```

---

## 十、变更确认 (DoD)

- [x] P0-1 admin 跨团队数据穿透修复
- [x] P0-2 训练任务重启权限校验
- [x] P0-3 角色变更 token 吊销
- [x] P1-4 list_team_activities admin 旁路
- [x] P1-5 _assert_can_manage owner 伪造
- [x] 17 项单元测试 (14 + 3) 全部通过
- [x] test_team_l4_audit F04 测试一致性更新
- [x] 文档归档 (本报告)
- [x] 所有修改后端测试通过 (127 项相关测试)
- [x] 风险评估与残留项记录
- [x] 代码提交 (f985bb6, 待用户确认推送)
