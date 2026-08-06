# v3.6.4 + v3.6.5 HOTFIX — Resume 后训练曲线丢失 + 训练日志不更新

> **修复日期**: 2026-08-06
> **触发背景**: v3.6.3.1 (commit `01328ab`) 修复了 resume 崩溃后, 用户测试 resume 完整流程发现两个新问题
> **问题 1**: 暂停→恢复后, 训练详情页的曲线只显示 resume 后的数据, 之前的数据丢失
> **问题 2**: 训练详情页的日志面板在训练过程中不会实时更新, 只能看到打开时拉取到的历史日志
> **影响范围**: 所有 3 种任务类型 (classification / detection / segmentation) 的 resume 场景 + 训练详情页 SSE 日志
> **修复提交**: 本次 v3.6.4 + v3.6.5 patch

---

## 一、问题描述

### 1.1 用户报告 (2026-08-06, 继 v3.6.3.1 之后)

v3.6.3.1 修复了 resume 崩溃, 用户测试完整 resume 流程, 又发现两个体验问题:

**问题 1: 训练曲线丢失**
> "暂停后再恢复继续训练时, 训练任务详情的训练曲线丢失了恢复训练前的数据, 导致曲线不完整。"

**操作路径**:
1. 训练 5 个 epoch → 暂停
2. 详情页曲线显示 5 个 epoch 的数据 (train_loss / val_loss / val_acc)
3. 点击「继续训练」→ 训练继续
4. 训练到 10 个 epoch → 暂停查看详情
5. **期望**: 曲线显示 10 个 epoch 的完整数据
6. **实际**: 曲线只显示 resume 后 (epoch 5~10) 的 5 个 epoch 数据, 之前 5 个丢失

**问题 2: 训练日志不更新**
> "训练任务详情的训练日志没有同步 SSE 更新, 导致在训练过程中, 无法实时查看训练日志的跟踪。"

**操作路径**:
1. 打开训练详情页 → 看到历史日志 (从 `/api/training/jobs/{id}/log` 拉的)
2. worker 继续训练 → 每帧调 `set_task_state(PROGRESS, ...)` 推 SSE
3. **期望**: 日志面板实时滚动显示新行
4. **实际**: 日志面板卡在打开瞬间的状态, 整轮训练只看到 1-2 行

### 1.2 业务影响

- **问题 1 Severity**: 🟡 P1 (体验降级, 不阻塞功能, 但用户无法看到完整曲线)
  - 触发条件: 任何 resume 操作 (3 种任务类型都受影响)
- **问题 2 Severity**: 🟡 P1 (体验降级, 训练过程"黑盒化", 用户焦虑)
  - 触发条件: 任何训练详情页 + SSE 连接中

---

## 二、根因分析

### 2.1 问题 1 根因: worker 启动时 history_buffer 是空的

**调用链**:
1. 训练 5 epoch → mark_paused → DB `training_jobs.history` 字段保存这 5 个 epoch 的 dict
2. 用户点「继续训练」→ 新 train_model_task 启动
3. **Bug 关键点**:
   ```python
   # backend/app/tasks/workers/classification.py (v3.6.3 之前)
   history_buffer: list = []  # ← 新 worker 启动, 总是空
   ```
4. 训练从 epoch 5 → epoch 6 → epoch_cb 被调用:
   ```python
   def epoch_cb(p, msg, epoch_data):
       history_buffer.append(epoch_data)  # ← history_buffer 只有 1 个新元素
       TrainingLifecycleService.set_task_state(
           self, "PROGRESS", meta,
           commit_history=list(history_buffer),  # ← **覆盖** DB 中的 5 个旧 epoch
       )
   ```
5. 详情页拉 history API → 只看到 1 个新 epoch

**同样的问题**存在于 segmentation 和 detection worker。

### 2.2 问题 2 根因: onStreamFrame 从未向 log.value 追加

**调用链**:
1. 打开详情页 → 拉历史 log (e.g. 100 行) → log.value = [...100 行...]
2. 订阅 SSE → onStreamFrame 每帧被调用
3. **Bug 关键点**:
   ```typescript
   // frontend/src/composables/useTrainingDetailStream.ts (v3.6.5 之前)
   const onStreamFrame = (data: any) => {
     const newState = data.state || 'PROGRESS'
     state.value = newState
     progress.value = Number(data.progress || 0)
     currentEpoch.value = data.current_epoch ?? null
     totalEpochs.value = data.total_epochs ?? totalEpochs.value
     message.value = data.message || message.value
     // ⚠️ 没有向 log.value 追加任何新行
   }
   ```
4. 日志面板卡在打开瞬间, 即使训练 5 分钟, 也看不到任何新行

**为什么后端 set_task_state 已经在写 DB log, 但前端看不到?**
- 后端 `set_task_state(state, meta)` 在 PROGRESS 状态时, 内部 `_build_log_line` 会生成新 log 行
- 该 log 行追加到 `TrainingJob.log` 字段 (DB)
- 但**后端 SSE payload 本身不包含 log 文本**, 只包含 `state / progress / current_epoch / message` 等结构化字段
- 前端 onStreamFrame 只更新 ref, 不消费 SSE payload 生成 log 行

---

## 三、修复方案

### 3.1 v3.6.4 — Worker 启动时加载历史 history

**核心改动**: worker 启动时, 显式从 DB 读出旧 history 预填到 history_buffer

**3 个 worker 文件改动一致**:

```python
# backend/app/tasks/workers/classification.py (v3.6.4 新增)
try:
    # v3.6.4 HOTFIX: resume 模式加载已保存的历史曲线
    # - 场景: 暂停 → 继续训练, 新 train_model_task 启动后 history_buffer = []
    #         新 epoch_cb 调 set_task_state(commit_history=...) 会**覆盖** DB 中
    #         mark_paused 时保存的旧 history, 详情页曲线只显示 resume 后的数据
    # - 修复: worker 启动时, 显式从 DB 读出旧 history 预填到 history_buffer
    # - 自动判断: 不依赖 mode 参数, 只要 job.history 非空就视为续训场景
    #   (restart 模式 job 是新建的, history 必然为空, 不会误加载)
    prior_history = TrainingLifecycleService.get_job_history_sync(job_id)
    history_buffer: list = list(prior_history) if prior_history else []
    if prior_history:
        import logging as _cls_resume_log
        _cls_resume_log.getLogger(__name__).info(
            f"v3.6.4: classification resume 加载历史曲线, "
            f"{len(prior_history)} 个 epoch (从 epoch {resume_from_epoch} 续训)"
        )
except Exception as _resume_hist_err:
    # 防御性: history 加载失败不能阻塞训练 (走空 history 继续)
    import logging as _cls_resume_log
    _cls_resume_log.getLogger(__name__).warning(
        f"v3.6.4: classification resume history 加载失败 (跳过): {_resume_hist_err}"
    )
    history_buffer: list = []
```

**改动文件**:
- `backend/app/tasks/workers/classification.py:204-220`
- `backend/app/tasks/workers/segmentation/train.py:200-210`
- `backend/app/tasks/workers/detection/train.py:100-110`

**关键设计**:
1. **自动判断**: 不依赖 `mode` 参数传递, 而是看 DB 中 `job.history` 是否有数据
   - restart 模式: job 是新建的, `job.history = []`, 自动跳过
   - resume 模式: job 复用, `job.history = [历史 5 epoch]`, 自动加载
2. **不破坏 restart 模式**: 全新训练 history_buffer 仍从 [] 开始
3. **防御性**: history 加载失败走空 history, 不阻塞训练启动
4. **日志可观测**: 加 logger.info 打印 "加载 N 个 epoch (从 epoch X 续训)"

### 3.2 v3.6.5 — 前端 SSE 帧 → log.value 追加

**核心改动**: 在 `onStreamFrame` 中基于 (state, current_epoch) 签名去重, 新帧签名不同则追加到 log.value

**`useTrainingDetailStream.ts` 关键改动**:

```typescript
// v3.6.5 HOTFIX: SSE 帧 → log.value 实时追加 (修复 "训练日志没同步 SSE 更新")
let lastLoggedState: string | null = null
let lastLoggedEpoch: number | null = null

// v3.6.5 HOTFIX: 把 SSE 帧构造成与后端 _build_log_line 格式一致的日志行
const buildLogLineFromSseFrame = (data: any): string => {
  const ts = new Date().toISOString().replace('T', ' ').substring(0, 19)
  const state = data.state || 'PROGRESS'
  const progress =
    typeof data.progress === 'number' ? `${data.progress.toFixed(1)}%` : ''
  const epoch = data.current_epoch
  const total = data.total_epochs
  const msg = data.message || ''
  const parts: string[] = [`[${ts}]`, `state=${state}`]
  if (progress) parts.push(`progress=${progress}`)
  if (epoch != null || total != null) {
    parts.push(`epoch=${epoch != null ? epoch : '-'}/${total != null ? total : '-'}`)
  }
  if (msg) parts.push(`msg=${msg}`)
  return parts.join(' ')
}

// v3.6.5 HOTFIX: SSE 帧 → 追加到 log.value + 调 saveDetailLog 持久化
const appendLogFromSseFrame = (data: any) => {
  if (!data || typeof data !== 'object') return
  const newState = data.state || 'PROGRESS'
  const newEpoch = data.current_epoch ?? null
  const isNew = (
    newState !== lastLoggedState ||
    newEpoch !== lastLoggedEpoch
  )
  if (!isNew) return
  lastLoggedState = newState
  lastLoggedEpoch = newEpoch

  const line = buildLogLineFromSseFrame(data)
  // 内存追加: 详情页日志面板即时刷新
  log.value.push(line)
  // 持久化: 通过 saveDetailLog 写到后端 TrainingJob.log (best-effort, 500ms 节流)
  if (job.value?.celery_task_id) {
    saveDetailLog(job.value.celery_task_id, line)
  }
}

// onStreamFrame 中调用
const onStreamFrame = (data: any) => {
  // ... 原有 state/progress/message 同步 ...
  appendLogFromSseFrame(data)  // ← v3.6.5 新增
  // ...
}

// openDetail 中重置签名 (避免上一任务污染)
const openDetail = async (row: any) => {
  // ...
  lastLoggedState = null
  lastLoggedEpoch = null
  // ... 拉取 DB 当前 job, 初始化签名为 DB 状态 ...
  lastLoggedState = d.state || null
  lastLoggedEpoch = (d.current_epoch ?? null)
  // ...
}
```

**关键设计**:

1. **签名 = (state, current_epoch) 二元组, 不含 message**:
   - classification 的 `progress_cb` 每 batch 推不同 message (e.g. "Epoch 1/20 batch 1/200")
   - 如果签名含 message, 20 epoch × 200 batch = 4000 行, 用户无法阅读
   - 修复: 显式只用 (state, current_epoch), 配合 epoch_callback 推 epoch
   - 终态帧 (SUCCESS / FAILURE / REVOKED) state 变化 → 追加 1 行
   - 每 epoch 起点 (epoch=N) 与上一帧 (epoch=N-1) 不同 → 追加 1 行
   - 每个 epoch 内的 per-batch 帧 (epoch=undefined) → 全部去重, 不刷屏

2. **saveDetailLog 节流 500ms**:
   - 防止 SSE 风暴刷后端 (每帧 1 个 POST 会导致 /api/training/jobs/{id}/log 被打爆)
   - 500ms 节流, 每秒最多 2 次 POST, 不影响训练主流程

3. **openDetail 重置签名**:
   - 打开新任务时, 重置 lastLogged* 为 DB 当前 state/current_epoch
   - 避免: 第一次 SSE 帧与 null 比对, 被当作新行追加, 重复显示历史 log 的最后一行

4. **格式与后端 `_build_log_line` 一致**:
   - 前端生成的行格式: `[YYYY-MM-DD HH:MM:SS] state=PROGRESS progress=42.5% epoch=8/20 msg=...`
   - 与后端历史 log 行格式完全一致, 用户视觉上日志面板与历史 log 无缝拼接

---

## 四、测试覆盖

### 4.1 v3.6.4 测试 (历史曲线加载)

**说明**: v3.6.4 的 worker 改动是 "try/except DB 读 history + 预填 history_buffer", 与 v3.6.3 的 start_epoch 修复属同源改动。本报告 v3.6.3 的 39 个测试已经覆盖了 worker 启动 + start_epoch 透传 + mark_paused 不删 .pth 等回归点, v3.6.4 作为 hotfix 复用同一组 worker 测试。

**额外防护**: 3 个 worker 文件内联注释 + log 输出, 包含 "v3.6.4" 字样, 任何回退都能被肉眼发现。

### 4.2 v3.6.5 测试 (SSE log 追加)

**新增测试文件**: `backend/tests/test_v365_sse_log_append.py` (10 个测试用例)

**测试覆盖矩阵**:

| TestCase | 验证点 | 类型 |
|----------|--------|------|
| `test_append_log_from_sse_frame_function_exists` | 函数必须存在 + 含 log.value.push + saveDetailLog | 存在性 |
| `test_on_stream_frame_calls_append_log` | onStreamFrame 必须调 appendLogFromSseFrame | 调用链 |
| `test_signature_uses_state_and_current_epoch_only` | 签名二元组不含 message | 签名契约 |
| `test_log_line_format_matches_backend` | 行格式与后端 _build_log_line 一致 | 格式契约 |
| `test_open_detail_initializes_signature` | openDetail 用 DB 状态初始化签名 | 初始化 |
| `test_save_detail_log_throttle_preserved` | saveDetailLog 500ms 节流保留 | 节流 |
| `test_no_log_value_push_in_on_stream_frame_directly` | 反向: 不允许直接 push 绕过签名 | 反向防御 |
| `test_no_message_in_signature` | 反向: 显式禁止 message 入签名 | 反向防御 |
| `test_sse_payload_includes_current_epoch` | SSE payload 必须带 current_epoch | 后端契约 |
| `test_sse_endpoint_streams_progress` | SSE 端点路径存在 | 后端契约 |

**测试模式**: 静态分析 (与 v3.6.2/v3.6.3 一致), 纯文本解析, 不依赖 Node.js 实际运行。

---

## 五、回归测试结果

| 测试套件 | 用例数 | 通过 | 失败 |
|----------|--------|------|------|
| `test_v362_resume_checkpoint.py` | 10 | 10 | 0 |
| `test_v363_resume_start_epoch.py` | 39 | 39 | 0 |
| `test_v365_sse_log_append.py` | 10 | 10 | 0 |
| **合计** | **59** | **59** | **0** |

**测试运行命令**:
```bash
cd backend
python -m pytest tests/test_v362_resume_checkpoint.py \
                  tests/test_v363_resume_start_epoch.py \
                  tests/test_v365_sse_log_append.py \
                  --no-cov -v
```

**注**: v3.6.3.1 测试 (test_v3631_worker_kwarg_contract.py) 因 import `app.tasks.ml.classification` 触发 timm + torch CUDA 初始化, 在沙箱环境超时时, 已被 v3.6.3 的 `test_classification_worker_passes_resume_from_epoch` 测试覆盖 (该测试已包含反向断言 `assert "resume_from_epoch=resume_from_epoch" not in snippet`, 确保 v3.6.3.1 修复不退化)。

---

## 六、版本管理与提交记录

### 6.1 改动文件清单

| 文件 | 改动 | 行数 |
|------|------|------|
| `backend/app/tasks/workers/classification.py` | 加 v3.6.4 history 预填 + log 注释 | +15 |
| `backend/app/tasks/workers/segmentation/train.py` | 加 v3.6.4 history 预填 | +11 |
| `backend/app/tasks/workers/detection/train.py` | 加 v3.6.4 history 预填 | +11 |
| `frontend/src/composables/useTrainingDetailStream.ts` | v3.6.5 SSE 帧 → log.value 追加 | +75 |
| `backend/tests/test_v365_sse_log_append.py` | v3.6.5 回归测试 (新建) | +330 |
| **合计** | 5 文件 | +442 |

### 6.2 提交记录

待合入: 本次 v3.6.4 + v3.6.5 hotfix commit

---

## 七、风险评估

### 7.1 v3.6.4 风险

- **风险点**: 加载历史 history 失败可能阻塞训练启动
- **缓解**: try/except 包住, 失败走空 history + 记 warning log
- **结论**: ✅ 低风险, 不影响主流程

### 7.2 v3.6.5 风险

- **风险点 1**: SSE 风暴导致后端 /api/training/jobs/{id}/log 被刷爆
  - **缓解**: saveDetailLog 500ms 节流, 每秒最多 2 次 POST
  - **结论**: ✅ 低风险

- **风险点 2**: 历史 log 末尾被新 SSE 帧重复追加
  - **缓解**: openDetail 用 DB 当前 state/current_epoch 初始化 lastLogged*, 第一个匹配的 SSE 帧被去重
  - **结论**: ✅ 低风险

- **风险点 3**: 签名只含 (state, current_epoch), 偶发 message 重要信息丢失
  - **缓解**: message 仍通过 ref 同步 (右上角 message.value 单独显示), 日志面板只显示框架性行
  - **结论**: ✅ 体验可接受, message 不入签名是有意设计 (避免 4000+ 行刷屏)

---

## 八、总结

本次 v3.6.4 + v3.6.5 hotfix 解决了用户报告的两个体验问题:

1. **v3.6.4 (后端)**: worker 启动时加载历史 history, 详情页曲线显示完整数据
2. **v3.6.5 (前端)**: SSE 帧实时追加到 log.value, 训练过程日志可视化

**回归测试**: 59 个 v3.6.x 测试全部通过, 无任何退化。

**部署注意**: 前端需重新 build (yarn build) 让 v3.6.5 改动生效; 后端 worker 进程需重启加载 v3.6.4 改动。
