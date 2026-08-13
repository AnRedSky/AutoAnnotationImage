# v3.6.3.1 HOTFIX — resume 续训参数名错位 bug 修复

---

## 一、问题描述

### 1.1 用户报告

v3.6.3 patch (commit `7efb000`) 修复了"训练从头跑 + 进度被清空"两个 bug, 但引入了一个新 bug:

**操作路径**:
1. 用户启动训练 → 正常训练中
2. 用户点击「暂停」→ 训练暂停, 进度保存为 25% (e.g.)
3. 用户点击「继续训练」→ worker 启动 → 训练崩溃
4. 错误信息: `TypeError: run_training() got an unexpected keyword argument 'resume_from_epoch'`

### 1.2 业务影响

- **Severity**: 🔴 P0 (完全阻塞 resume 功能)
- **触发条件**: v3.6.3 patch + 任何 classification 任务 resume 操作
- **影响范围**: classification 任务 (segmentation/detection 未受影响, 见 §1.3)

### 1.3 根因 (写错一个 kwarg)

v3.6.3 patch 修改了三个 worker 让它们透传断点续训参数:

| Worker | ML 函数 | v3.6.3 写法 | 实际函数签名 | 状态 |
|--------|---------|-------------|--------------|------|
| `workers/segmentation/train.py:231` | `train_segmentation` | `start_epoch=resume_from_epoch` ✓ | `start_epoch` | OK |
| `workers/detection/train.py:212` | `train_yolo` | `start_epoch=resume_from_epoch` ✓ | `start_epoch` | OK |
| **`workers/classification.py:220`** | **`run_training`** | **`resume_from_epoch=resume_from_epoch` ✗** | **`start_epoch`** | **BUG** |

**v3.6.3 patch 修改时漏改**: classification worker 直接复制了 Celery task 入参名 (`resume_from_epoch=...`) 而没有翻译成 ML 层签名 (`start_epoch=...`)。segmentation/detection 写对了 (做了 `start_epoch=resume_from_epoch` 的命名翻译), 只有 classification 漏了。

### 1.4 为什么测试没捕获

v3.6.3 patch 的回归测试 ([test_v363_resume_start_epoch.py](file:///d:/works/WorkBuddy/Myhome/ThesisDesignImplementation/thesis-image-annotation/backend/tests/test_v363_resume_start_epoch.py)) 验证的是 **`calc_resume_from_epoch` 纯函数** (计算 epoch 起点), 完全没有验证 **worker → ML 函数的 kwarg 命名契约**。这是测试覆盖的盲点。

---

## 二、修复方案

### 2.1 代码修复 (1 行)

[workers/classification.py:220-224](file:///d:/works/WorkBuddy/Myhome/ThesisDesignImplementation/thesis-image-annotation/backend/app/tasks/workers/classification.py#L220-L224):

```diff
  pretrained_model_path=pretrained_model_path,
  # v3.6.3: 断点续训起始 epoch (0-based), 与 pretrained_model_path 配套
  # resume 时模型从 checkpoint 加载, 然后从 start_epoch 处继续训练
- resume_from_epoch=resume_from_epoch,
+ # v3.6.3.1 HOTFIX: 之前写错为 resume_from_epoch=..., run_training 实际签名是 start_epoch=
+ #   → TypeError: run_training() got an unexpected keyword argument 'resume_from_epoch'
+ #   → 与 segmentation/detection worker 对齐: worker 入参 resume_from_epoch (业务语义)
+ #     透传到 ML 层时改名为 start_epoch (ML 层语义)
+ start_epoch=resume_from_epoch,
```

**核心改动**: `resume_from_epoch=resume_from_epoch` → `start_epoch=resume_from_epoch`

**命名语义说明**:
- **Worker 入参 (Celery task)**: `resume_from_epoch` (业务语义, 强调是 resume 场景的起点)
- **ML 层 (run_training)**: `start_epoch` (内部语义, 强调是训练循环起点)
- **翻译发生在 worker 内部**: 业务层 → ML 层

### 2.2 新增参数名契约测试 (4 类 / 9 个测试)

[test_v3631_worker_kwarg_contract.py](file:///d:/works/WorkBuddy/Myhome/ThesisDesignImplementation/thesis-image-annotation/backend/tests/test_v3631_worker_kwarg_contract.py) — 用 AST 静态扫描 worker 源码, 自动对比 ML 函数真实签名:

**核心原理**:
```python
# 1) AST 提取 worker 调 ML 函数的所有 kwarg
worker_kwargs = _extract_call_kwargs(WORKER_PATH, "run_training")

# 2) inspect 提取 ML 函数真实签名
func_params = inspect.signature(run_training).parameters

# 3) 断言: worker_kwargs ⊆ func_params
invalid = worker_kwargs - func_params
assert not invalid, f"非法 kwarg: {invalid}"
```

**测试覆盖**:

| 测试类 | 测试方法 | 防回归场景 |
|--------|----------|------------|
| `TestClassificationWorkerContract` | `test_run_training_kwargs_all_valid` | 任何 worker → ML 函数的 kwarg 错配 (覆盖 v3.6.3 bug 类型) |
| `TestClassificationWorkerContract` | `test_resume_from_epoch_translated_to_start_epoch` | **显式断言** worker 翻译了 `resume_from_epoch` → `start_epoch` |
| `TestClassificationWorkerContract` | `test_worker_task_accepts_resume_from_epoch` | 业务层 Celery task 仍接受 `resume_from_epoch` 入参 |
| `TestSegmentationWorkerContract` | `test_train_segmentation_kwargs_all_valid` | segmentation 同类问题防护 |
| `TestSegmentationWorkerContract` | `test_resume_from_epoch_translated_to_start_epoch` | segmentation 显式断言 |
| `TestDetectionWorkerContract` | `test_train_yolo_kwargs_all_valid` | detection 同类问题防护 |
| `TestDetectionWorkerContract` | `test_resume_from_epoch_translated_to_start_epoch` | detection 显式断言 |
| `TestCrossWorkerConsistency` | `test_all_workers_handle_resume_from_epoch_consistently` | 三 worker 命名一致性 |
| `TestCrossWorkerConsistency` | `test_all_workers_pass_pretrained_model_path` | 三 worker 都传 `pretrained_model_path` (v3.6.2 引入) |

**契约测试优势**:
- ✅ **静态分析**: 不依赖 DB/Celery/Redis/torch 实际运行, 纯 AST 解析, < 1s 跑完
- ✅ **自动扩展**: 任何 worker 调 ML 函数时 kwarg 名错都会立即失败
- ✅ **双向保护**: 既防止 worker 写错, 又防止 ML 函数改名时忘改 worker
- ✅ **零依赖**: 不需要 conftest, 不需要 fixture, 不需要 import 整个 ML 模块

### 2.3 验证修复

**修复前 (v3.6.3)**: worker 源码:
```python
result = run_training(
    ...,
    pretrained_model_path=pretrained_model_path,
    resume_from_epoch=resume_from_epoch,  # ← TypeError!
    ...
)
```

**修复后 (v3.6.3.1)**: worker 源码:
```python
result = run_training(
    ...,
    pretrained_model_path=pretrained_model_path,
    start_epoch=resume_from_epoch,  # ← 命名翻译
    ...
)
```

**契约测试反向验证** (手动模拟 buggy 状态):
```
=== 模拟 buggy 状态: worker 传 resume_from_epoch= ===
  ✓ 测试能捕获 bug: 非法 kwargs {'resume_from_epoch'}
```

---

## 三、测试结果

### 3.1 契约测试 (新增)

```
[TestClassificationWorkerContract]
  PASS: test_resume_from_epoch_translated_to_start_epoch
  PASS: test_run_training_kwargs_all_valid
  PASS: test_worker_task_accepts_resume_from_epoch
[TestCrossWorkerConsistency]
  PASS: test_all_workers_handle_resume_from_epoch_consistently
  PASS: test_all_workers_pass_pretrained_model_path
[TestDetectionWorkerContract]
  PASS: test_resume_from_epoch_translated_to_start_epoch
  PASS: test_train_yolo_kwargs_all_valid
[TestSegmentationWorkerContract]
  PASS: test_resume_from_epoch_translated_to_start_epoch
  PASS: test_train_segmentation_kwargs_all_valid

总计: 9 passed, 0 failed
```

### 3.2 既有测试覆盖

- ✅ v3.6.3 `test_v363_resume_start_epoch.py` 39 项继续通过 (验证 epoch 计算正确性)
- ✅ v3.6.2 `test_v362_resume_checkpoint.py` 4 类继续通过 (验证 checkpoint 路径解析)
- ✅ v3.6.1 `test_job_race_condition.py` 7 项继续通过 (验证 race condition 兜底)

---

## 四、文件清单

### 4.1 代码改动 (1 个文件)

| 文件 | 改动 | 说明 |
|------|------|------|
| [backend/app/tasks/workers/classification.py](file:///d:/works/WorkBuddy/Myhome/ThesisDesignImplementation/thesis-image-annotation/backend/app/tasks/workers/classification.py) | `resume_from_epoch=...` → `start_epoch=resume_from_epoch` | 1 行实际改动, 注释扩充到 5 行说明命名语义 |

### 4.2 新增测试 (1 个文件)

| 文件 | 规模 | 说明 |
|------|------|------|
| [backend/tests/test_v3631_worker_kwarg_contract.py](file:///d:/works/WorkBuddy/Myhome/ThesisDesignImplementation/thesis-image-annotation/backend/tests/test_v3631_worker_kwarg_contract.py) | 4 测试类 / 9 个测试方法 | AST 静态契约测试, 防止 worker → ML 函数 kwarg 错配 |

### 4.3 文档 (本节件 + 计划更新)

- 本节件: `docs/task-summaries/017-fix-resume-kwarg-name-v3.6.3.1.md`
- 计划更新: `plan-c-train-perf-v3.6.0.md` 附录 D

---

## 五、经验教训

### 5.1 命名翻译是显式契约, 不是隐式约定

Worker 入参 (业务语义) 和 ML 函数入参 (内部语义) 名字不同时, **必须** 在 worker 内部显式做命名翻译:

```python
# ✗ 错误: 直接透传 (v3.6.3 bug)
result = ml_func(resume_from_epoch=resume_from_epoch)

# ✓ 正确: 显式翻译 (v3.6.3.1 fix)
result = ml_func(start_epoch=resume_from_epoch)
```

### 5.2 测试覆盖盲点

v3.6.3 的 39 个测试都是**纯函数测试** (验证 epoch 计算逻辑), 没有验证**集成调用** (worker → ML 函数 kwarg 命名契约)。这是测试覆盖盲点, v3.6.3.1 补充了 AST 静态契约测试来覆盖。

### 5.3 防御性编程原则

任何 worker 调 ML 函数时, 都应假设 ML 函数签名可能变化, 配套契约测试是 **零成本** 的保险。

---

## 六、版本号

- **修复版本**: v3.6.3.1 (patch level 增量)
- **下次合并**: 建议合入下一个 minor release (v3.6.4 或 v3.7.0)
- **回滚方案**: 单行改动, `git revert <commit>` 即可回滚

---

**报告生成时间**: 2026-08-06
**修复耗时**: ~30 分钟 (1 行代码 + 1 个测试文件 + 1 个文档)
**测试覆盖**: 9/9 PASSED, 0 回归
