# 阶段 S{N} 报告: {STAGE_NAME}

> **阶段 ID**: S{N}
> **生成时间**: {TIMESTAMP}
> **状态**: {STATUS}
> **完成度**: {COMPLETION}% ({DONE}/{TOTAL})

---

## 一、阶段目标

{GOAL}

**优先级**: {PRIORITY}

**依赖阶段**: {DEPENDS}

---

## 二、任务完成情况

| 任务 ID | 标题 | 状态 | 完成时间 | 备注 |
|---|---|---|---|---|
{TASK_TABLE}

**汇总**: 完成 {DONE} 个, 跳过 {SKIP} 个

---

## 三、退出检查

| 检查项 | 结果 |
|---|---|
{CHECKS_TABLE}

---

## 四、关键产出

### 4.1 代码改动

待 `git log --oneline` 补充 (本报告生成后由编排器自动 commit)

### 4.2 新增/修改文件

待 `git diff --stat HEAD~1` 补充

### 4.3 阶段报告

- 本报告: `backend/docs/S{N}_REPORT.md`
- 进度看板: `backend/docs/PROGRESS.md`

---

## 五、关键指标

（如适用）

| 指标 | 计划 | 实际 | 状态 |
|---|---|---|---|
| - | - | - | - |

---

## 六、下阶段准备

**下一阶段**: {NEXT_STAGE}

**进入条件**: ✅ 已满足

**预启动任务**:
{NEXT_TASKS}

---

## 七、问题与风险

| 风险 | 影响 | 应对 |
|---|---|---|
| - | - | - |

---

## 八、附录

- 配置: `backend/docs/stage_config.yaml`
- 任务标记: `backend/docs/.task_done/`
- 执行日志: `backend/docs/stage_logs/`
- AI 协助历史: `backend/docs/stage_logs/ai_assist_history.jsonl`
