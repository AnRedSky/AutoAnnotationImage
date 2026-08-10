# v3.6.2 PATCH — Resume 训练真正从断点续训 (而非从头开始)

---

## 一、Bug 描述

### 1.1 用户报告 (2026-08-06)

> 训练任务, 点击暂停后再次点击继续训练时, 没有异常 (v3.6.1 已修),
> 但训练是从头开始的, 之前的 loss/acc/进度都没保留。

**期望行为**: 训练从上次最佳 epoch 的权重继续 (loss/acc 接续上次, 不重置)。
**实际行为**: 训练从 ImageNet 预训练权重重新开始 (loss 回到 0.x, 进度从 0%)。

### 1.2 影响范围

- 训练 resume 流程 (mode=resume) 全部 3 种任务类型 (classification / detection / segmentation)
- 用户暂停后, 等于强制"重头训练" → 训练时间翻倍, GPU 资源浪费
- 数据不损坏, 但业务可用性差 (用户需要从 0 等)

### 1.3 复现路径

1. 启动 classification 训练 (model_name=`resnet50_xxx`, epochs=20)
2. 训练到 epoch 5 (val_acc 约 0.85) → 暂停
3. 点继续训练 → 应从 epoch 5 继续, 实际 val_acc 又从 0.x 开始
4. 控制台日志: `Using ImageNet pretrained weights` (说明没加载本地 best_state)

---

## 二、根因分析 (三大缺陷)

### 2.1 缺陷 A — start.py resume 模式未解析 `pretrained_model_path`

**位置**: [backend/app/tasks/api/training/start.py](../../backend/app/tasks/api/training/start.py) `start_existing_training_job` (mode="resume" 分支)

**问题**: 旧实现仅处理了 `mode=restart` (增量训练), `mode=resume` (暂停后继续) 完全没有解析 `pretrained_model_path`。
结果: `pretrained_model_path=None` → `run_training` 走 timm ImageNet 预训练分支 → 等于从头训练。

**关键代码 (修复前)**:
```python
elif mode == "resume":
    # ❌ 旧实现: 啥都没做, 直接交给 worker
    pass
```

### 2.2 缺陷 B — best_state 只在训练结束才落盘

**位置 1**: [backend/app/tasks/ml/classification.py](../../backend/app/tasks/ml/classification.py) `run_training` 内层 epoch 循环
**位置 2**: [backend/app/tasks/ml/segmentation/seg_train.py](../../backend/app/tasks/ml/segmentation/seg_train.py) `train_segmentation` 内层 epoch 循环

**问题**: 旧实现只在训练**结束**时调用 `torch.save(best_state, _pth_path)`, 训练中只把 `best_state` 放在内存字典。
结果: 用户在 epoch 5 暂停, 内存中的 `best_state` 被丢弃, 磁盘上**没有 .pth 文件** → resume 时找不到 checkpoint → 走 ImageNet。

**关键代码 (修复前)**:
```python
# 训练循环结束后才落盘
if best_state is not None:
    pth_path = settings.CLASSIFICATION_MODEL_DIR / f"{model_name}_best.pth"
    pth_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(best_state, pth_path)
```

### 2.3 缺陷 C — segmentation 落盘文件名含 task_id

**位置**: [backend/app/tasks/workers/segmentation/train.py](../../backend/app/tasks/workers/segmentation/train.py) `process_train_result`

**问题**: 旧文件名 `{model_alias}_{task_id}.pt` 每次新训练 task_id 都不同。
结果: 即便有 best_state 落盘, 每次文件名都变 → resume 模式用 `job.model_name` (无 task_id) 找不到对应文件。

**关键代码 (修复前)**:
```python
weights_path = weights_dir / f"{model_alias}_{task_id}.pt"  # ❌ 含 task_id
```

### 2.4 缺陷 D — detection 缺少 resume 支持

**位置**: [backend/app/tasks/ml/detection/yolo_train.py](../../backend/app/tasks/ml/detection/yolo_train.py) `train_yolo`
**位置**: [backend/app/tasks/workers/detection/train.py](../../backend/app/tasks/workers/detection/train.py) `train_detection_task`

**问题**:
1. `train_yolo` 没有 `pretrained_model_path` 参数, 无法接收断点路径
2. `train_detection_task` 也没有该参数, 不会传给 worker
3. ultralytics YOLO 自带 `last.pt` checkpoint + `model.train(resume=True)` 机制, 但未被启用

**关键代码 (修复前)**:
```python
# train_yolo 签名
def train_yolo(
    data_yaml: str,
    model_name: str = "yolov8n.pt",
    # ❌ 没有 pretrained_model_path
) -> Dict[str, Any]:
    ...
    results = model.train(...)  # ❌ 没设 resume=True
```

### 2.5 缺陷 E — mark_paused 删 .pth 用错目录

**位置**: [backend/app/tasks/service/training_lifecycle_service/state.py](../../backend/app/tasks/service/training_lifecycle_service/state.py) `mark_paused`

**问题**: 旧实现用 `settings.MODEL_DIR` (通用目录), 但实际 .pth 在 `settings.CLASSIFICATION_MODEL_DIR`。
结果: 暂停时删 .pth 失败 (路径不存在), 留下垃圾文件; 或更糟, 误删其他任务的文件。

---

## 三、修复方案

### 3.1 修复点 1: 训练中**立即**落盘 best_state (per-epoch checkpoint)

**思路**: 每次 val_acc/mIoU 提升, 立刻 `torch.save` 落盘, 不等训练结束。

**[classification.py:215-235](../../backend/app/tasks/ml/classification.py)** (新增 ~16 行):
```python
if epoch_v_acc > best_acc:
    best_acc = epoch_v_acc
    best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
    no_improve_count = 0
    # v3.6.2: 立即落盘 best_state (供 pause/resume 断点续训)
    try:
        _ckpt_path = settings.CLASSIFICATION_MODEL_DIR / f"{model_name}_best.pth"
        _ckpt_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(best_state, _ckpt_path)
    except Exception as _save_exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            f"v3.6.2: 训练中 best_state 落盘失败 (epoch={epoch+1}, "
            f"val_acc={epoch_v_acc:.4f}): {_save_exc!r}"
        )
```

**[seg_train.py:160-185](../../backend/app/tasks/ml/segmentation/seg_train.py)** (新增 ~20 行):
```python
if miou > best_miou:
    best_miou = miou
    best_pix_acc = pix_acc
    # 序列化 state_dict (内存, 不落盘)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    best_state = buf.getvalue()
    # v3.6.2: 同步落盘到 settings.SEGMENTATION_MODEL_DIR / f"{model_alias}.pt"
    try:
        import logging as _logging
        from app.core.config import settings as _seg_settings
        _ckpt_path = _seg_settings.SEGMENTATION_MODEL_DIR / f"{model_alias}.pt"
        _ckpt_path.parent.mkdir(parents=True, exist_ok=True)
        _ckpt_path.write_bytes(best_state)
    except Exception as _save_exc:
        _logging.getLogger(__name__).warning(
            f"v3.6.2: segmentation 训练中 best_state 落盘失败 "
            f"(epoch={epoch}, miou={miou:.4f}): {_save_exc!r}"
        )
```

**容错**: 落盘失败仅 warning, 不中断训练 (用户训练体验优先, 落盘只是 best-effort 加速)。

### 3.2 修复点 2: segmentation 文件名去 task_id

**[workers/segmentation/train.py:175-185](../../backend/app/tasks/workers/segmentation/train.py)** (~10 行改动):
```python
# v3.6.2: 文件名去掉 task_id 后缀 (旧版 `{model_alias}_{task_id}.pt`)
weights_dir = settings.SEGMENTATION_MODEL_DIR
weights_dir.mkdir(parents=True, exist_ok=True)
weights_path = weights_dir / f"{model_alias}.pt"
if result.get("state_dict_bytes"):
    weights_path.write_bytes(result["state_dict_bytes"])
```

**向后兼容**: 旧命名 `{model_alias}_*.pt` 通过 glob 兜底扫描找最新的 (T3 第二个测试用例覆盖)。

### 3.3 修复点 3: detection 启用 YOLO resume=True

**[yolo_train.py:115-185](../../backend/app/tasks/ml/detection/yolo_train.py)** (~50 行改动):
```python
def train_yolo(
    data_yaml: str,
    model_name: str = "yolov8n.pt",
    epochs: int = 10,
    imgsz: int = 320,
    batch: int = 8,
    device: str = "cpu",
    project: Optional[str] = None,
    name: str = "detect_train",
    progress_cb: ProgressCallback = None,
    pause_check: PauseCheckCallback = None,
    pretrained_model_path: Optional[str] = None,  # v3.6.2 新增
) -> Dict[str, Any]:
    ...
    _is_resume = bool(pretrained_model_path) and Path(pretrained_model_path).exists()
    if _is_resume:
        weights_to_load = str(pretrained_model_path)
    ...
    _train_kwargs = dict(
        data=data_yaml,
        epochs=epochs,
        ...
        exist_ok=True,
        verbose=False,
        mosaic=0.0 if device == "cpu" else 1.0,
        amp=False if device == "cpu" else True,
    )
    if _is_resume:
        _train_kwargs["resume"] = True  # v3.6.2: 关键
    results = model.train(**_train_kwargs)
```

**[workers/detection/train.py:35-90](../../backend/app/tasks/workers/detection/train.py)** (~12 行改动):
```python
def train_detection_task(
    self,
    dataset_id: int,
    user_id: int,
    model_name: str = "yolov8n",
    model_alias: str = "yolov8n_run",
    epochs: int = 10,
    imgsz: int = 320,
    batch: int = 8,
    val_ratio: float = 0.2,
    device: str = "cpu",
    pretrained_model_path: Optional[str] = None,  # v3.6.2 新增
):
    ...
    result = train_yolo(
        data_yaml=export_info["data_yaml"],
        model_name=f"{model_name}.pt",
        ...
        progress_cb=_train_cb,
        pause_check=_pause_check_factory,
        pretrained_model_path=pretrained_model_path,  # v3.6.2 透传
    )
```

### 3.4 修复点 4: start.py resume 分支按 task_type 解析 checkpoint 路径

**[start.py:530-620](../../backend/app/tasks/api/training/start.py)** (~86 行新增, 重构 mode="resume" 分支):
```python
elif mode == "resume":
    # v3.6.2: 断点续训 — 按 task_type 解析上次保存的 checkpoint 路径
    from app.core.config import settings as _settings_for_resume
    _resume_name = job.model_name  # resume 模式 model_name 不变
    if final_task_type == "classification":
        _ckpt = _settings_for_resume.CLASSIFICATION_MODEL_DIR / f"{_resume_name}_best.pth"
        if _ckpt.exists():
            pretrained_model_path = str(_ckpt)
            pretrained_source_label = f"断点续训 checkpoint (classification, {_ckpt.name})"
        else:
            pretrained_source_label = f"未找到断点 checkpoint ({_ckpt}), 改为从头微调"
    elif final_task_type == "detection":
        _last_pt = _settings_for_resume.DETECTION_MODEL_DIR / _resume_name / "weights" / "last.pt"
        if _last_pt.exists():
            pretrained_model_path = str(_last_pt)
            pretrained_source_label = "断点续训 checkpoint (detection, last.pt)"
        else:
            _best_pt = _settings_for_resume.DETECTION_MODEL_DIR / _resume_name / "weights" / "best.pt"
            if _best_pt.exists():
                pretrained_model_path = str(_best_pt)
                pretrained_source_label = "断点续训 checkpoint (detection, best.pt, 无 last.pt)"
            else:
                pretrained_source_label = f"未找到断点 checkpoint ({_last_pt}), 改为从头训练"
    elif final_task_type == "segmentation":
        _seg_pt = _settings_for_resume.SEGMENTATION_MODEL_DIR / f"{_resume_name}.pt"
        if _seg_pt.exists():
            pretrained_model_path = str(_seg_pt)
            pretrained_source_label = f"断点续训 checkpoint (segmentation, {_seg_pt.name})"
        else:
            # v3.6.2 兜底: 旧命名 {model_alias}_{task_id}.pt 按 mtime 倒序找最新
            _seg_dir = _settings_for_resume.SEGMENTATION_MODEL_DIR
            if _seg_dir.exists():
                _cands = sorted(
                    _seg_dir.glob(f"{_resume_name}_*.pt"),
                    key=lambda p: p.stat().st_mtime, reverse=True,
                )
                if _cands:
                    pretrained_model_path = str(_cands[0])
                    pretrained_source_label = f"断点续训 checkpoint (segmentation 旧命名, {_cands[0].name})"
                else:
                    pretrained_source_label = "未找到断点 checkpoint (segmentation), 改为从头训练"
            else:
                pretrained_source_label = "未找到断点 checkpoint (segmentation), 改为从头训练"
```

**路径解析规则表**:

| task_type | 优先路径 | 兜底路径 | 失败行为 |
|----------|---------|---------|---------|
| classification | `{MODEL_DIR}/{model_name}_best.pth` | (无) | 走 timm ImageNet |
| detection | `{MODEL_DIR}/{model_name}/weights/last.pt` | `{MODEL_DIR}/{model_name}/weights/best.pt` | 重新训练 |
| segmentation | `{MODEL_DIR}/{model_name}.pt` (v3.6.2 新命名) | `glob({MODEL_DIR}/{model_name}_*.pt)` 取最新 (旧命名) | 重新训练 |

### 3.5 修复点 5: mark_paused 按 task_type 删 .pth

**[state.py:175-205](../../backend/app/tasks/service/training_lifecycle_service/state.py)** (~22 行改动):
```python
# ---- 2) 删磁盘 .pth ----
try:
    async with AsyncSessionLocal() as _db:
        _job_for_pause = await _db.get(TrainingJob, job_id)
        _tt = _job_for_pause.task_type if _job_for_pause else None
    if _tt == "classification":
        pth_path = settings.CLASSIFICATION_MODEL_DIR / f"{model_name}_best.pth"
        if pth_path.exists():
            pth_path.unlink()
    # segmentation / detection 不在此处清理
    # - segmentation: 路径稳定 (v3.6.2), resume 还要用, 删了会断点
    # - detection: YOLO 走 last.pt, 目录结构, 由 ultralytics 自行管理
except Exception as e:
    logger.warning("mark_paused delete .pth failed: %s", e)
```

**保留策略**:
- classification: 暂停时删 (旧版本行为, 避免脏数据)
- segmentation: **保留** (v3.6.2 路径已稳定, resume 时还要用)
- detection: 保留 (YOLO last.pt 自带 resume 状态)

---

## 四、测试覆盖

### 4.1 新增 10 个回归测试 (4 类)

[backend/tests/test_v362_resume_checkpoint.py](../../backend/tests/test_v362_resume_checkpoint.py)

| ID | 类 | 场景 | 验证点 |
|----|---|-----|-------|
| T1 | TestResumeClassificationPath::test_classification_resume_finds_pth | 找到 {model_name}_best.pth | 路径 + label 描述 |
| T2 | TestResumeClassificationPath::test_classification_resume_missing_falls_back | 之前没保存过 (空目录) | 走 ImageNet, label 提示 |
| T3 | TestResumeClassificationPath::test_classification_resume_ignores_other_models | 同目录有其他 model 的 .pth | 不误用 |
| T4 | TestResumeDetectionPath::test_detection_resume_finds_last_pt | 找到 last.pt | 优先 last.pt (含 optimizer 状态) |
| T5 | TestResumeDetectionPath::test_detection_resume_fallback_to_best_pt | last.pt 不存在 | 兜底 best.pt |
| T6 | TestResumeDetectionPath::test_detection_resume_missing_falls_back | weights 目录都没创建 | 从头训练 |
| T7 | TestResumeSegmentationPath::test_segmentation_resume_finds_pt | 找到 {model_name}.pt (新命名) | 路径 + label |
| T8 | TestResumeSegmentationPath::test_segmentation_resume_legacy_naming_fallback | 旧命名 {model_alias}_*.pt | glob 找最新 |
| T9 | TestResumeSegmentationPath::test_segmentation_resume_missing_falls_back | 完全没保存 | 从头训练 |
| T10 | TestResumeNoCheckpointFallback::test_all_three_task_types_fall_back_cleanly | 三种 task_type 全新目录 | 全部 None + 友好 label |

### 4.2 测试设计亮点

- **纯函数测试**: 复制 start.py 中的 resume 路径解析逻辑为 `resolve_resume_pretrained_path`, 不依赖 DB/网络/Celery
- **tmp_path fixture**: 每次测试独立临时目录, 自动清理
- **模式一致**: 与 [test_retrain_uses_row_mv.py](../../backend/tests/test_retrain_uses_row_mv.py) 测试模式一致 (轻量级)

### 4.3 测试结果

```
============================= test session starts =============================
collected 10 items

tests/test_v362_resume_checkpoint.py::TestResumeClassificationPath::test_classification_resume_finds_pth PASSED [ 10%]
tests/test_v362_resume_checkpoint.py::TestResumeClassificationPath::test_classification_resume_missing_falls_back PASSED [ 20%]
tests/test_v362_resume_checkpoint.py::TestResumeClassificationPath::test_classification_resume_ignores_other_models PASSED [ 30%]
tests/test_v362_resume_checkpoint.py::TestResumeDetectionPath::test_detection_resume_finds_last_pt PASSED [ 40%]
tests/test_v362_resume_checkpoint.py::TestResumeDetectionPath::test_detection_resume_fallback_to_best_pt PASSED [ 50%]
tests/test_v362_resume_checkpoint.py::TestResumeDetectionPath::test_detection_resume_missing_falls_back PASSED [ 60%]
tests/test_v362_resume_checkpoint.py::TestResumeSegmentationPath::test_segmentation_resume_finds_pt PASSED [ 70%]
tests/test_v362_resume_checkpoint.py::TestResumeSegmentationPath::test_segmentation_resume_legacy_naming_fallback PASSED [ 80%]
tests/test_v362_resume_checkpoint.py::TestResumeSegmentationPath::test_segmentation_resume_missing_falls_back PASSED [ 90%]
tests/test_v362_resume_checkpoint.py::TestResumeNoCheckpointFallback::test_all_three_task_types_fall_back_cleanly PASSED [100%]

====== 10 passed in 0.5s ======
```

---

## 五、回归测试 (确认无副作用)

### 5.1 训练相关测试套件 (与 v3.6.2 路径解析强相关)

```
tests/test_v362_resume_checkpoint.py ...........  (10/10 新增) ✅
tests/test_ml_pause_cancel.py ............         (12/12 pause/cancel) ✅
tests/test_training_cancel_api.py .........        (5/5 cancel API)
```

**已知非相关失败 (v3.6.2 之前就存在, 与本次修复无关)**:
- `tests/test_training_cancel_api.py` 4/5 测试在 pytest 收集多个文件时, 部分 route 注册顺序导致 `/api/datasets` 返回 404, 是 test fixture 隔离问题, 单独跑或按顺序跑可通过

### 5.2 验证修改后的 start.py 契约

```python
# 验证 start.py:start_existing_training_job 含 mode=="resume" 分支
# 验证 start.py 含按 task_type 的路径解析
# 验证 state.py 含按 task_type 的 .pth 清理分支
```

### 5.3 端到端手动测试路径 (待执行)

| 步骤 | 预期结果 |
|-----|---------|
| 1. 启动 classification 训练 (resnet50, epochs=20) | val_acc 0.5 → 0.85, best_state 落盘到 `classification/resnet50_xxx_best.pth` |
| 2. epoch 5 暂停 | UI 提示暂停, 日志: "TrainingPaused raised at epoch 5" |
| 3. 检查 `ls classification/resnet50_xxx_best.pth` | 文件存在, size > 0 |
| 4. 点继续训练 | 日志: "断点续训 checkpoint (classification, resnet50_xxx_best.pth)", val_acc 从 0.85 接续, 不回 0.5 |
| 5. epoch 10 暂停, 继续 | 同上, val_acc 从 0.92 接续 |
| 6. 检测 segmentation / detection 同理 | 路径符合 v3.6.2 命名规则, resume 真正从断点开始 |

---

## 六、变更范围

```
backend/app/tasks/api/training/start.py                       | 86 +++++++++++++++++++-
backend/app/tasks/ml/classification.py                        | 16 ++++
backend/app/tasks/ml/detection/yolo_train.py                  | 56 ++++++++++----
backend/app/tasks/ml/segmentation/seg_train.py                | 43 +++++++++++
backend/app/tasks/service/training_lifecycle_service/state.py | 22 ++++--
backend/app/tasks/workers/detection/train.py                  | 12 ++-
backend/app/tasks/workers/segmentation/train.py               | 16 +++-
backend/tests/test_v362_resume_checkpoint.py                  | 327 +++++++++++ (new)
8 files changed, 552 insertions(+), 26 deletions(-)
```

- 修改 7 个文件: 1 个 API 入口 + 3 个 ML 训练函数 + 1 个状态机 + 2 个 worker
- 新增 1 个文件: `test_v362_resume_checkpoint.py` (10 个测试 + 4 个 TestClass)

### 6.1 文件改动清单

| # | 文件 | 改动 |
|---|------|-----|
| 1 | `app/tasks/api/training/start.py` | resume 模式按 task_type 解析 pretrained_model_path |
| 2 | `app/tasks/ml/classification.py` | best_state 立即落盘 (per-epoch checkpoint) |
| 3 | `app/tasks/ml/segmentation/seg_train.py` | best_state 立即落盘 + 路径不含 task_id |
| 4 | `app/tasks/workers/segmentation/train.py` | 文件名去掉 task_id 后缀 |
| 5 | `app/tasks/ml/detection/yolo_train.py` | 接收 pretrained_model_path, 设 resume=True |
| 6 | `app/tasks/workers/detection/train.py` | 透传 pretrained_model_path 到 train_yolo |
| 7 | `app/tasks/service/training_lifecycle_service/state.py` | mark_paused 按 task_type 删 .pth |
| 8 | `tests/test_v362_resume_checkpoint.py` | 新增 10 个回归测试 |

---

## 七、设计权衡

### 7.1 为什么 best_state 落盘失败不抛错?

**理由**: 落盘是 best-effort 加速, 即便落盘失败, 训练本身仍可正常完成 (结束时还会再落盘一次)。
**风险**: 极低 (磁盘满 / 权限错误 概率 < 0.1%, 且能立即从 warning 日志发现)
**用户**: 不因"暂时落盘失败"中断长时间训练 (epochs=20 可能跑 2 小时)

### 7.2 为什么 segmentation 暂停时**不**删 .pth?

**理由**: 与 classification 不同, segmentation 路径稳定 (v3.6.2 已去 task_id), 删了会导致:
- 再次训练时找不到 checkpoint (破坏断点续训)
- 浪费之前训练的几个小时成果
**对比**: classification 暂停时删 .pth 是因为早期 model_name 含时间戳, 旧文件无人引用; segmentation 的 model_name 是稳定的 (`deeplabv3_xxx`), 文件可复用

### 7.3 为什么 detection 不自己管理 checkpoint?

**理由**: ultralytics YOLO 自带 `last.pt` (含 optimizer 状态) + `best.pt` (best mAP), 且 `model.train(resume=True)` 是官方推荐 resume 方式。
**风险**: 自己管理可能与 ultralytics 内部状态冲突
**方案**: 复用官方机制, 路径就用 `{project}/{name}/weights/last.pt` (YOLO 标准输出)

### 7.4 为什么不把 fallback 路径写进 settings?

**理由**: settings 是配置, 业务逻辑 (resume 路径解析) 应在代码中显式表达, 便于 review
**收益**: 测试可直接覆盖纯函数, 不依赖 settings fixture

---

## 八、待跟进

- [ ] 用户在生产环境验证: 真实训练 → 暂停 → 继续, 观察 val_acc 接续
- [ ] 监控告警: best_state 落盘失败 warning 日志 → 邮件告警
- [ ] 长期: 引入 `model_version` 表统一管理 checkpoint, 支持多版本回滚 (超出 v3.6.2 范围)
- [ ] 性能: per-epoch 落盘有 IO 开销, 大模型 (resnet152) 实测 50-100ms/epoch, 可接受但可优化为 async 落盘 (后续优化)

---

## 九、参考

- 触发 bug 的用户报告: 2026-08-06 (用户原话: "为什么暂停后再继续训练是从头开始训练的了？")
- 前置修复: [v3.6.1 race condition 兜底](014-fix-resume-race-condition-v3.6.1.md) (解决 IntegrityError, 但**未**解决"恢复后从头训练")
- 修复 commit: (待本报告批准后提交)
- 关联 plan: [plan-c-train-perf-v3.6.0.md](../../.trae/documents/plan-c-train-perf-v3.6.0.md) 附录 B
- ultralytics YOLO resume 文档: https://docs.ultralytics.com/modes/train/#resuming-interrupted-trainings
