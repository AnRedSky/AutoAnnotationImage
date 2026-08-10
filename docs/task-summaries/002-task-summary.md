# 任务总结报告 #002 - L2 阶段团队管理增强

---

## 一、上一次任务的完成情况（L1 阶段承接）

### 1.1 上一份报告（#001 - L1 阶段）执行回顾

| 计划项 | L1 实际完成 | 备注 |
|---|---|---|
| 数据库迁移（4 字段 + 3 索引） | ✅ 完成 | `add_team_enhance.py` |
| 后端 4 个新端点（PATCH/transfer/leave/datasets） | ✅ 完成 | `team.py` |
| 11 端点加审计（log_audit） | ✅ 完成 | 11 个写操作全覆盖 |
| 集成测试 20 用例 | ✅ 完成 | `test_team_management.py` 全过 |
| 前端拆分 Teams.vue → 子组件 | ✅ 完成 | 6 个子组件，单文件 < 1000 行 |
| 前端 6 个新弹窗 | ✅ 完成 | Create/Edit/Invite/EditRole/Transfer/Share |
| 路由切换 + 旧文件 fallback | ✅ 完成 | `router/index.ts` |

**已达成目标**：L1 阶段 P0 全部交付，数据隔离、权限控制、审计日志三大基础能力建立。

**L1 已知限制（L2 阶段补齐）**：
1. ❌ 缺少用户搜索端点 → 邀请时用户选择体验差
2. ❌ 缺少团队级统计 → 无法可视化团队工作量
3. ❌ 最后一名 manager 保护缺失 → 团队可能失去所有管理者
4. ❌ 数据集详情页没有团队共享入口 → 需先进入团队页才能分享

---

## 二、当前任务的具体实施计划（L2 阶段）

### 2.1 任务目标

按 [09-02-团队管理-项目实现计划.md](../09-02-团队管理-项目实现计划.md) Phase L2 完成团队管理增强，含 5 个子任务：

| 任务 ID | 任务名 | 预期成果 |
|---|---|---|
| L2-T1 | 团队统计端点 `GET /api/stats/team/{id}` | 含数据隔离 + 8 类指标聚合 |
| L2-T2 | 团队统计端点集成测试 | 12 用例覆盖权限/聚合/边界 |
| L2-T3 | 前端 ECharts 团队统计卡片 | TeamStatsCard.vue + 3 个图表 |
| L2-T4 | Teams 页面集成统计 Tab | "数据统计" Tab + 时间范围切换 |
| L2-T5 | DatasetDetail 集成团队共享入口 | hero 加分享按钮 + 弹窗 |

### 2.2 实施步骤

| 步骤 | 内容 | 预期成果 |
|---|---|---|
| Step 1 | 团队统计端点实现 | `_assert_team_member_or_403` 复用团队数据隔离 |
| Step 2 | 12 个集成测试用例 | 权限/空/单数据集/多数据集/不合格分离/排名/趋势/边界/admin |
| Step 3 | ECharts 团队统计卡片 | 4 KPI + AI 节省 + 3 图表 (饼图/柱状图/折线) |
| Step 4 | Teams 页面增加"数据统计" Tab | 单文件 < 1000 行约束保持 |
| Step 5 | DatasetHero 改造 | 仅 owner 可见分享按钮，弹窗由父组件控制 |
| Step 6 | 联合调试 | 49 测试全过（stats + user_search + team_management） |

### 2.3 风险与对策

| 风险 | 对策 |
|---|---|
| 统计端点涉及多表 JOIN，性能 | 通过 `team_id` 索引 + 单端点限制 1 个 Team 范围 |
| ECharts 集成复杂度 | 复用 Dashboard 的 `echarts.init()` 模式 |
| 组件代码行数膨胀 | 新组件 TeamStatsCard 控制在 < 350 行 |

---

## 三、当前任务的实际完成情况

### 3.1 交付物清单

#### 后端文件

| 文件 | 类型 | 说明 |
|---|---|---|
| [backend/app/admin/api/stats.py](../../backend/app/admin/api/stats.py) | 修改 | 新增 `GET /api/stats/team/{team_id}` 端点 + `_assert_team_member_or_403` 助手 |
| [backend/tests/test_team_stats.py](../../backend/tests/test_team_stats.py) | 新建 | 12 个集成测试用例 (T01-T12) |

#### 前端文件

| 文件 | 类型 | 说明 |
|---|---|---|
| [frontend/src/api/index.ts](../../frontend/src/api/index.ts) | 修改 | 新增 `statsApi.team()` + 3 个 TS interface (TeamStats 等) |
| [frontend/src/views/Admin/Teams/components/TeamStatsCard.vue](../../frontend/src/views/Admin/Teams/components/TeamStatsCard.vue) | 新建 | 4 KPI + AI 节省 Alert + 3 个 ECharts 图表 |
| [frontend/src/views/Admin/Teams/index.vue](../../frontend/src/views/Admin/Teams/index.vue) | 修改 | 新增"数据统计" Tab + 时间范围切换 (7/14/30 天) |
| [frontend/src/views/DatasetDetail/components/DatasetHero.vue](../../frontend/src/views/DatasetDetail/components/DatasetHero.vue) | 修改 | 新增"分享到团队"按钮 (仅 owner 可见) + emit('share') |
| [frontend/src/views/DatasetDetail/index.vue](../../frontend/src/views/DatasetDetail/index.vue) | 修改 | 集成 ShareDatasetDialog + isDatasetOwner 计算 + 加载分享数据 |

### 3.2 关键设计决策

#### 3.2.1 团队统计端点的数据隔离

**问题**：之前的统计端点（dataset/confidence/timeline）都用 `assert_can_access_dataset` 校验单数据集权限；团队统计需要聚合多数据集，不能用单数据集校验。

**方案**：复用团队成员校验逻辑，引入 `_assert_team_member_or_403` 助手函数，与 `team.py` 的 `_get_member_or_403` 保持一致语义（数据隔离：admin 也不绕过）。

```python
async def _assert_team_member_or_403(
    db: AsyncSession, team_id: int, user_id: int
) -> TeamMember:
    """校验当前用户是团队成员 (数据隔离: 非成员不可访问, 包括 admin)."""
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(403, "无权限: 非团队成员")
    return member
```

#### 3.2.2 不合格图片正交分离

**问题**：v3.0.0 引入 `quality_flag` 字段后，状态分布的逻辑必须保持向后兼容（饼图不显示不合格，但单独统计）。

**方案**：在 `team_stats` 中 `status_counts` 用 `Image.quality_flag.is_(None)` 过滤，`unqualified_count` 单独查询，**与 dataset_stats 端点完全一致**。

#### 3.2.3 ECharts 团队统计卡片的低耦合设计

**问题**：前端组件拆分规范要求底层组件不直接 useStore、不直接调 API。

**方案**：让 `TeamStatsCard.vue` 内部自管数据加载（props 透传 teamId + days），父组件只控制挂载。这样：
- 父组件不需要透传大量 props
- 子组件可独立测试/复用
- 符合"业务组件允许自调 API" 规则（§三-1-2 业务通用组件）

**权衡**：与严格"页面统一发请求"原则略有妥协，但团队统计是只读数据展示，没有写操作不会污染数据流，权衡后保留此设计。

#### 3.2.4 分享按钮的权限控制

**问题**：DatasetDetail 页面可能有多人协作（owner + team members），但只有 owner 可以把数据集分享给团队。

**方案**：在 `DatasetHero` 加 `isOwner` prop，**只在 owner 视角下显示分享按钮**。`isDatasetOwner` 由父组件基于 `dataset.owner_id === userStore.user.id` 计算。

```vue
<el-button
  v-if="props.isOwner"
  :icon="Share"
  @click="emit('share')"
>
  分享到团队
</el-button>
```

### 3.3 实际完成情况 vs 计划

| 任务 ID | 计划 | 实际 | 偏差 |
|---|---|---|---|
| L2-T1 | 团队统计端点 | ✅ 完成 | 0 |
| L2-T2 | 12 个集成测试 | ✅ 12 用例全过 | 0 |
| L2-T3 | ECharts 团队统计卡片 | ✅ 完成 (337 行) | 0 |
| L2-T4 | Teams 页面集成 Tab | ✅ 完成 (index.vue 426 行) | 0 |
| L2-T5 | DatasetDetail 分享入口 | ✅ 完成 (DatasetDetail 606 行) | 0 |
| L2-T6 | 联合调试 + 报告 | ✅ 完成 (49 测试全过) | 0 |

### 3.4 测试结果

```
tests/test_team_stats.py     12 PASSED   (新增)
tests/test_user_search.py    11 PASSED   (L2 早期交付)
tests/test_team_management.py 26 PASSED  (L1 交付, L2 未引入回归)
─────────────────────────────────────────
Total: 49 passed in 105.83s
```

### 3.5 已知遗留问题（不阻塞 L2 交付）

1. **统计端点无缓存**：每次切换团队都重新查 DB，未来可加 Redis 缓存（5 分钟过期）
2. **ECharts 体积**：项目已引入 echarts 全量包（~700KB），未来可按需引入
3. **分享状态反查**：当前用 `dataset.team_id` 单值，未来数据集支持多团队共享时需重构

---

## 四、下一步的详细工作计划

### 4.1 Phase L3 任务规划（待执行）

按 [09-02-团队管理-项目实现计划.md](../09-02-团队管理-项目实现计划.md) Phase L3，剩余任务：

| 任务 | 内容 | 优先级 | 估时 |
|---|---|---|---|
| L3-T1 | 团队软删除/归档 | P1 | 0.5d |
| L3-T2 | archived_at 端点集成 (DELETE → 软删) | P1 | 0.5d |
| L3-T3 | 团队列表分页 + 搜索 | P1 | 0.5d |
| L3-T4 | 团队列表按角色/成员数排序 | P2 | 0.3d |
| L3-T5 | 团队详情 Redis 缓存 | P2 | 0.3d |
| L3-T6 | 软删除数据可恢复 (管理员工具) | P2 | 0.5d |
| L3-T7 | L3 阶段联合调试 + 报告 | P1 | 0.5d |

**总估时**：3.1 人日

### 4.2 L3 关键设计要点

1. **软删除流程**：
   - DELETE `/api/teams/{id}` → 设置 `archived_at = now()` 而非硬删
   - 列表端点自动过滤 `archived_at IS NULL`
   - 管理员专用 `/api/teams/{id}/restore` 恢复

2. **分页策略**：
   - 服务端：`?page=1&page_size=20&search=...`
   - 客户端：复用现有 `Pagination` 组件
   - 搜索：`name LIKE %q% OR slug LIKE %q%`

3. **缓存策略**：
   - 缓存键：`team:detail:{team_id}` 5 分钟 TTL
   - 失效时机：PATCH/DELETE/成员变更
   - 避免：统计端点（数据频繁变化，不适合缓存）

### 4.3 立即可执行的下一步

按依赖顺序：

1. **L3-T1 软删除字段已存在**（L1 已加 `archived_at` 字段），直接修改 DELETE 端点
2. **L3-T3 分页**：需先看现有 `team.py` 列表端点，扩展支持分页参数
3. **L3-T5 缓存**：需要先确认 Redis 配置，引入 `app.core.cache` 模块

### 4.4 风险评估

| 风险 | 等级 | 应对 |
|---|---|---|
| 软删除改造可能影响现有逻辑 | 中 | 端点行为向后兼容（archived=NULL 视为正常） |
| 分页+搜索引入新参数，前端需同步 | 中 | 与前端协商同时改，统一字段命名 |
| Redis 缓存失效逻辑错误 | 低 | 用 L1 阶段的事件驱动失效 |

---

## 五、附录

### 5.1 关键代码片段

#### 后端：团队统计端点签名

```python
@router.get("/team/{team_id}")
async def team_stats(
    team_id: int,
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """团队级统计 (v3.3.1 L2) — 仅团队成员可访问"""
    # 1. 权限校验 (数据隔离)
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    await _assert_team_member_or_403(db, team_id, current_user.id)
    # 2. 聚合数据集/图片/状态分布/贡献者/趋势/AI 节省时间
    ...
```

#### 前端：ECharts 团队统计卡片

```vue
<template>
  <div class="team-stats-card" v-loading="loading">
    <el-row :gutter="16" class="kpi-row">
      <el-col :span="6">
        <div class="kpi-card">
          <div class="kpi-label">数据集</div>
          <div class="kpi-value">{{ stats.dataset_count }}</div>
        </div>
      </el-col>
      <!-- 3 more KPI cards -->
    </el-row>
    <el-row :gutter="16" class="chart-row">
      <el-col :span="8"><div ref="statusChartRef" /></el-col>
      <el-col :span="8"><div ref="contributorChartRef" /></el-col>
      <el-col :span="8"><div ref="timelineChartRef" /></el-col>
    </el-row>
  </div>
</template>
```

### 5.2 文件行数审计

| 文件 | 行数 | 规范限制 | 合规 |
|---|---|---|---|
| backend/app/admin/api/stats.py | 614 | < 2000 (api) | ✅ |
| backend/tests/test_team_stats.py | 700 | 无限制 | ✅ |
| frontend/src/views/Admin/Teams/index.vue | 426 | < 1000 (views) | ✅ |
| frontend/src/views/Admin/Teams/components/TeamStatsCard.vue | 337 | < 1000 (components) | ✅ |
| frontend/src/views/DatasetDetail/index.vue | 606 | < 1000 (views) | ✅ |
| frontend/src/views/DatasetDetail/components/DatasetHero.vue | 134 | < 1000 (components) | ✅ |

### 5.3 提交信息模板

```
feat(team): v3.3.1 L2 阶段 - 团队管理增强

- 后端: 新增 GET /api/stats/team/{id} 端点 (8 类指标)
- 后端: 12 个集成测试用例 (权限/聚合/边界/不合格分离)
- 前端: 新增 TeamStatsCard 组件 (4 KPI + 3 ECharts 图表)
- 前端: Teams 页面新增"数据统计" Tab + 时间范围切换
- 前端: DatasetDetail 集成团队共享入口 (仅 owner 可见)

测试: 49 passed (stats + user_search + team_management)
```

---

**报告完成时间**: 2026-08-05
**下一阶段**: L3 - 软删除/分页/缓存优化
**下一份报告**: `003-task-summary.md` (预计完成 L3 后输出)
