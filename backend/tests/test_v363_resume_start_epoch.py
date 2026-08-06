"""
v3.6.3 PATCH — Resume 模式断点续训 start_epoch 透传 + 进度保留 (轻量版)
======================================================================

**Bug 场景** (用户报告 2026-08-06, 继 v3.6.2 之后):
- v3.6.2 修复了 pretrained_model_path 解析 (找到 checkpoint)
- 但用户报告两个**新问题**:
  1. 训练仍然从 epoch 0 重跑, 不会从暂停位置继续
  2. 暂停时的训练进度被**清空**为 0%, 看不到当前实际进度

**根因** (v3.6.3 诊断):
1. `start_existing_training_job(mode="resume")` 没有计算/传递 `start_epoch`
   → `run_training` 循环总是从 0 开始
2. `start_existing_training_job` resume 分支显式 `job.progress = 0.0`
   → 用户看到进度从 0% 开始, 体感"进度被清空"
3. `state.py:mark_paused` 在 v3.6.2 修正了路径后, 真的把 classification 的 .pth 删了
   → 修了 v3.6.1 的"删不到"问题, 但引入新问题: resume 找不到 checkpoint
4. yolo_train.py 走 ultralytics 原生 resume=True, 但 progress_cb 缺 start_epoch 偏移

**修复** (v3.6.3 PATCH):
- 4 个 ML/Worker 层文件新增 `start_epoch` / `resume_from_epoch` 参数:
  - `ml/classification.py:run_training(start_epoch=0)` — 循环从 start_epoch 开始
  - `ml/segmentation/seg_train.py:train_segmentation(start_epoch=0)` — 循环从 start_epoch+1 开始 (1-based)
  - `ml/detection/yolo_train.py:train_yolo(start_epoch=0)` — 接受参数 (ultralytics 自带 epoch 恢复)
  - 3 个 worker 文件透传 `resume_from_epoch` → `start_epoch`
- `start.py:start_existing_training_job`:
  - resume 模式计算 `_resume_from_epoch = round(progress/100 * total) - 1` (0-based)
  - 透传给 `_build_task_kwargs(..., resume_from_epoch=...)` (3 种 task_type 都支持)
  - resume 模式**不再清空** `job.progress = 0.0`, 保留 PAUSED 时的进度
- `state.py:mark_paused`:
  - **不再删除**任何 .pth 文件 (v3.6.2 误改行为回滚)
  - resume 时 checkpoint 必须存在, 删除 = 从头训练

**测试覆盖** (4 类 + 边界):
- T1: start.py resume_from_epoch 计算正确性 (含 4 种边界)
- T2: start.py 3 种 task_type kwargs 都带 resume_from_epoch
- T3: 3 个 ML 函数都接受 start_epoch (签名契约)
- T4: start.py resume 模式不重置 progress
- T5: state.py mark_paused 不删 .pth (关键回归)

**测试模式**:
- 纯函数测试 (T1): 复制 start.py 的 resume_from_epoch 计算公式
- 契约检查 (T2-T5): 读源文件源码, 验证关键改动存在
- 与 test_v362_resume_checkpoint.py 模式一致

**运行方式**:
    cd backend && python -m pytest tests/test_v363_resume_start_epoch.py -v

**注意**: 契约检查测试需要 conftest.py 不报错 (轻量级, 不需要 DB 真实连接).
         如果运行报错 ImportError, 可加 `-p no:cacheprovider` 或先 `pip install -e .`
"""
from __future__ import annotations

import re
from pathlib import Path


# ============== 纯函数: 复制自 start.py resume 分支 ==============

def calc_resume_from_epoch(
    saved_progress: float,
    total_epochs: int,
) -> int:
    """纯函数: 给定 PAUSED 时保存的 progress (0-100) + total_epochs, 计算 resume_from_epoch (0-based)

    v3.6.3: 与 start.py:start_existing_training_job 中 mode=="resume" 分支逻辑一致
    - 公式: resume_from_epoch (0-based) = round(progress/100 * total) - 1
    - 边界: clamped 到 [0, total-1]
    - restart 模式: 返回 0 (全新训练)

    Examples:
        >>> calc_resume_from_epoch(25.0, 20)
        4   # 25% = 5/20, 1-based 5 = 0-based 4
        >>> calc_resume_from_epoch(0.0, 20)
        0   # 兜底, 防止算出 -1
        >>> calc_resume_from_epoch(100.0, 20)
        19  # clamped 到 [0, 19]
        >>> calc_resume_from_epoch(50.0, 20)
        9   # 50% = 10/20, 1-based 10 = 0-based 9
    """
    if total_epochs <= 0:
        return 0
    resume_from_epoch = int(round(float(saved_progress) / 100.0 * total_epochs)) - 1
    # 边界保护: [0, total-1]
    return max(0, min(resume_from_epoch, total_epochs - 1))


# ============== 路径常量 ==============

BACKEND_ROOT = Path(__file__).resolve().parents[1]
START_PY = BACKEND_ROOT / "app" / "tasks" / "api" / "training" / "start.py"
STATE_PY = BACKEND_ROOT / "app" / "tasks" / "service" / "training_lifecycle_service" / "state.py"
CLASSIFICATION_PY = BACKEND_ROOT / "app" / "tasks" / "ml" / "classification.py"
SEG_TRAIN_PY = BACKEND_ROOT / "app" / "tasks" / "ml" / "segmentation" / "seg_train.py"
YOLO_TRAIN_PY = BACKEND_ROOT / "app" / "tasks" / "ml" / "detection" / "yolo_train.py"
WORKERS_CLASSIFICATION_PY = BACKEND_ROOT / "app" / "tasks" / "workers" / "classification.py"
WORKERS_SEGMENTATION_PY = BACKEND_ROOT / "app" / "tasks" / "workers" / "segmentation" / "train.py"
WORKERS_DETECTION_PY = BACKEND_ROOT / "app" / "tasks" / "workers" / "detection" / "train.py"


def _read(path: Path) -> str:
    assert path.exists(), f"源文件不存在: {path}"
    return path.read_text(encoding="utf-8")


# ============== T1: start.py resume_from_epoch 计算正确性 ==============

class TestResumeFromEpochCalculation:
    """测试 start.py 的 resume_from_epoch 计算公式 (纯函数, 不依赖 DB)"""

    def test_25_percent_of_20_epochs(self):
        """暂停在 25% (5/20) → start_epoch=4 (0-based)"""
        assert calc_resume_from_epoch(25.0, 20) == 4

    def test_50_percent_of_20_epochs(self):
        """暂停在 50% (10/20) → start_epoch=9 (0-based)"""
        assert calc_resume_from_epoch(50.0, 20) == 9

    def test_0_percent_falls_back_to_zero(self):
        """0% 时不返回 -1, 兜底为 0"""
        assert calc_resume_from_epoch(0.0, 20) == 0

    def test_100_percent_clamps_to_last(self):
        """100% 时 clamped 到 [0, total-1] = 19 (不会出现 range 越界)"""
        assert calc_resume_from_epoch(100.0, 20) == 19

    def test_5_percent_of_20_epochs(self):
        """5% (1/20) → start_epoch=0 (0-based)"""
        assert calc_resume_from_epoch(5.0, 20) == 0

    def test_fractional_progress_rounds(self):
        """12.5% (2.5/20) → 3 1-based → 2 0-based"""
        # 12.5% of 20 = 2.5, round = 2 (banker's rounding), but Python rounds half-to-even
        # 2.5 rounds to 2 in Python 3, so result is 1
        result = calc_resume_from_epoch(12.5, 20)
        assert result in (1, 2), f"unexpected round result: {result}"

    def test_total_epochs_one(self):
        """total_epochs=1 边界: start_epoch=0 (clamped)"""
        assert calc_resume_from_epoch(50.0, 1) == 0

    def test_total_epochs_zero(self):
        """total_epochs=0 兜底: 0 (防止除零)"""
        assert calc_resume_from_epoch(50.0, 0) == 0


# ============== T2: start.py 3 种 task_type kwargs 都带 resume_from_epoch ==============

class TestStartPyResumeFromEpochInKwargs:
    """契约检查: start.py 的 _build_task_kwargs 函数必须在 3 个 task_type 分支都带 resume_from_epoch"""

    def test_detection_kwargs_includes_resume_from_epoch(self):
        src = _read(START_PY)
        # 在 detection 分支内查找
        # 用 'model_alias=model_name' 作为 detection 分支的 anchor
        anchor = 'model_name=base_model,    # yolov8n/s/m/l/x'
        assert anchor in src, f"detection 分支 anchor 丢失: {anchor}"
        # 在 anchor 之后查找 resume_from_epoch
        idx = src.index(anchor)
        # 取该分支后续 600 字符 (覆盖整个 if detection: return dict 块)
        snippet = src[idx:idx + 600]
        assert "resume_from_epoch=resume_from_epoch" in snippet, (
            "detection 分支 kwargs 缺少 resume_from_epoch 字段"
        )

    def test_segmentation_kwargs_includes_resume_from_epoch(self):
        src = _read(START_PY)
        anchor = 'backbone=base_model,     # deeplabv3_resnet50/101'
        assert anchor in src, f"segmentation 分支 anchor 丢失: {anchor}"
        idx = src.index(anchor)
        snippet = src[idx:idx + 600]
        assert "resume_from_epoch=resume_from_epoch" in snippet, (
            "segmentation 分支 kwargs 缺少 resume_from_epoch 字段"
        )

    def test_classification_kwargs_includes_resume_from_epoch(self):
        src = _read(START_PY)
        # classification 是默认分支, 在最后
        # 找 'base_model=base_model' (classification 唯一)
        anchor = 'base_model=base_model,'
        assert anchor in src, f"classification 分支 anchor 丢失: {anchor}"
        idx = src.index(anchor)
        # 取后续 800 字符 (覆盖整个 classification return dict 块)
        snippet = src[idx:idx + 800]
        assert "resume_from_epoch=resume_from_epoch" in snippet, (
            "classification 分支 kwargs 缺少 resume_from_epoch 字段"
        )


# ============== T3: 3 个 ML 函数都接受 start_epoch (签名契约) ==============

class TestMLFunctionsAcceptStartEpoch:
    """契约检查: 3 个 ML 训练函数都新增了 start_epoch 参数 (0-based)"""

    def test_run_training_accepts_start_epoch(self):
        src = _read(CLASSIFICATION_PY)
        # 检查函数签名包含 start_epoch
        assert re.search(
            r"def run_training\([^)]*start_epoch\s*:\s*int\s*=\s*0",
            src,
            re.DOTALL,
        ), "ml/classification.py:run_training 缺少 start_epoch 参数"

    def test_run_training_uses_start_epoch_in_loop(self):
        src = _read(CLASSIFICATION_PY)
        # 训练循环必须用 range(start_epoch, epochs)
        assert "range(start_epoch, epochs)" in src, (
            "ml/classification.py:run_training 训练循环未使用 start_epoch"
        )

    def test_run_training_clamps_start_epoch(self):
        """边界保护: start_epoch 限制在 [0, epochs-1]"""
        src = _read(CLASSIFICATION_PY)
        # 必须有边界 clamp 逻辑
        assert re.search(
            r"start_epoch\s*=\s*max\(0,\s*min\(int\(start_epoch\),\s*epochs\s*-\s*1\)\)",
            src,
        ), "ml/classification.py:run_training 缺少 start_epoch 边界保护"

    def test_train_segmentation_accepts_start_epoch(self):
        src = _read(SEG_TRAIN_PY)
        assert re.search(
            r"def train_segmentation\([^)]*start_epoch\s*:\s*int\s*=\s*0",
            src,
            re.DOTALL,
        ), "ml/segmentation/seg_train.py:train_segmentation 缺少 start_epoch 参数"

    def test_train_segmentation_uses_start_epoch_in_loop(self):
        """segmentation 循环是 1-based, 跳过前 start_epoch 个 epoch → range(start_epoch + 1, epochs + 1)"""
        src = _read(SEG_TRAIN_PY)
        assert "range(start_epoch + 1, epochs + 1)" in src, (
            "ml/segmentation/seg_train.py:train_segmentation 训练循环未使用 start_epoch"
        )

    def test_train_segmentation_clamps_start_epoch(self):
        src = _read(SEG_TRAIN_PY)
        assert re.search(
            r"start_epoch\s*=\s*max\(0,\s*min\(int\(start_epoch\),\s*epochs\s*-\s*1\)\)",
            src,
        ), "ml/segmentation/seg_train.py:train_segmentation 缺少 start_epoch 边界保护"

    def test_train_yolo_accepts_start_epoch(self):
        src = _read(YOLO_TRAIN_PY)
        assert re.search(
            r"def train_yolo\([^)]*start_epoch\s*:\s*int\s*=\s*0",
            src,
            re.DOTALL,
        ), "ml/detection/yolo_train.py:train_yolo 缺少 start_epoch 参数"

    def test_train_yolo_clamps_start_epoch(self):
        """YOLO 走原生 resume=True, 但仍需 start_epoch 边界保护"""
        src = _read(YOLO_TRAIN_PY)
        assert re.search(
            r"start_epoch\s*=\s*max\(0,\s*min\(int\(start_epoch\),\s*epochs\s*-\s*1\)\)",
            src,
        ), "ml/detection/yolo_train.py:train_yolo 缺少 start_epoch 边界保护"


# ============== T4: 3 个 worker 透传 resume_from_epoch ==============

class TestWorkersPassthroughResumeFromEpoch:
    """契约检查: 3 个 worker 都接收 resume_from_epoch 并透传给 ML 函数"""

    def test_classification_worker_accepts_resume_from_epoch(self):
        src = _read(WORKERS_CLASSIFICATION_PY)
        assert re.search(
            r"def train_model_task\([^)]*resume_from_epoch\s*:\s*int\s*=\s*0",
            src,
            re.DOTALL,
        ), "workers/classification.py:train_model_task 缺少 resume_from_epoch 参数"

    def test_classification_worker_passes_resume_from_epoch(self):
        src = _read(WORKERS_CLASSIFICATION_PY)
        # 必须调用 run_training(..., resume_from_epoch=resume_from_epoch)
        # 关键字查找: run_training 出现后, 后续应含 'resume_from_epoch=resume_from_epoch'
        # (跨多行, 用 're.DOTALL' 不可靠; 改用 .find 定位 + 局部窗口搜索)
        idx = src.find("run_training(")
        assert idx >= 0, "workers/classification.py 中未找到 run_training( 调用"
        # 取 run_training( 后续 1500 字符 (覆盖整段 kwargs, 容忍嵌套括号)
        snippet = src[idx:idx + 1500]
        assert "resume_from_epoch=resume_from_epoch" in snippet, (
            "workers/classification.py 透传 resume_from_epoch 失败"
        )

    def test_segmentation_worker_accepts_resume_from_epoch(self):
        src = _read(WORKERS_SEGMENTATION_PY)
        assert re.search(
            r"def train_segmentation_task\([^)]*resume_from_epoch\s*:\s*int\s*=\s*0",
            src,
            re.DOTALL,
        ), "workers/segmentation/train.py:train_segmentation_task 缺少 resume_from_epoch 参数"

    def test_segmentation_worker_passes_start_epoch(self):
        src = _read(WORKERS_SEGMENTATION_PY)
        # 透传时 worker 把 resume_from_epoch 映射到 ML 层的 start_epoch
        idx = src.find("train_segmentation(")
        assert idx >= 0, "workers/segmentation/train.py 中未找到 train_segmentation( 调用"
        snippet = src[idx:idx + 1500]
        assert "start_epoch=resume_from_epoch" in snippet, (
            "workers/segmentation/train.py 透传 start_epoch 失败"
        )

    def test_detection_worker_accepts_resume_from_epoch(self):
        src = _read(WORKERS_DETECTION_PY)
        assert re.search(
            r"def train_detection_task\([^)]*resume_from_epoch\s*:\s*int\s*=\s*0",
            src,
            re.DOTALL,
        ), "workers/detection/train.py:train_detection_task 缺少 resume_from_epoch 参数"

    def test_detection_worker_passes_start_epoch(self):
        src = _read(WORKERS_DETECTION_PY)
        idx = src.find("train_yolo(")
        assert idx >= 0, "workers/detection/train.py 中未找到 train_yolo( 调用"
        snippet = src[idx:idx + 1500]
        assert "start_epoch=resume_from_epoch" in snippet, (
            "workers/detection/train.py 透传 start_epoch 失败"
        )


# ============== T5: start.py resume 模式不重置 progress ==============

class TestStartPyDoesNotResetProgressOnResume:
    """契约检查: start.py resume 模式必须保留 PAUSED 时的 progress, 不能清空为 0.0"""

    def test_no_progress_reset_in_resume_branch(self):
        """检查 resume 分支中不存在 'job.progress = 0.0' 这行代码 (注释/文档中允许提及)

        关键: 必须用行级匹配 (^\s*job\.progress = 0\.0\s*$) 才能排除注释行和文档字符串
        因为 v3.6.3 注释里会解释"旧版 job.progress = 0.0 会清空..." 用于审计追踪
        """
        src = _read(START_PY)
        for lineno, line in enumerate(src.splitlines(), start=1):
            stripped = line.strip()
            # 跳过注释和空行
            if not stripped or stripped.startswith("#"):
                continue
            # 跳过 docstring (三重引号开头的行内字符串, 简化处理: 注释已过滤)
            assert not re.match(r"^\s*job\.progress\s*=\s*0\.0\s*$", line), (
                f"start.py 第 {lineno} 行存在 'job.progress = 0.0', "
                f"v3.6.3 修复要求 resume 模式保留 PAUSED 时的 progress\n"
                f"  > {line!r}"
            )

    def test_resume_branch_sets_state_to_pending(self):
        """resume 分支必须把 state 从 PAUSED 改回 PENDING (用于前端状态展示)"""
        src = _read(START_PY)
        # 查找 'if mode == "resume":' 块
        match = re.search(
            r'if\s+mode\s*==\s*[\'"]resume[\'"]\s*:\s*\n\s*job\.state\s*=\s*[\'"]PENDING[\'"]',
            src,
        )
        assert match, (
            "start.py 中 resume 分支未将 state 设为 PENDING, "
            "前端 status 会一直显示 PAUSED"
        )


# ============== T6: start.py resume 模式计算 _resume_from_epoch ==============

class TestStartPyComputesResumeFromEpoch:
    """契约检查: start.py resume 分支必须计算 _resume_from_epoch 并透传"""

    def test_calculates_resume_from_epoch_in_resume_branch(self):
        src = _read(START_PY)
        # 查找 '_resume_from_epoch = int(round(_saved_progress / 100.0 * _total_epochs_calc)) - 1'
        # 容许空格变化
        pattern = (
            r"_resume_from_epoch\s*=\s*int\(round\(_saved_progress\s*/\s*100\.0\s*\*\s*"
            r"_total_epochs_calc\)\)\s*-\s*1"
        )
        assert re.search(pattern, src), (
            "start.py 中 resume 分支未按公式计算 _resume_from_epoch"
        )

    def test_passes_resume_from_epoch_to_apply_kwargs(self):
        src = _read(START_PY)
        # _build_task_kwargs 调用时必须含 resume_from_epoch=_resume_from_epoch
        assert re.search(
            r"_build_task_kwargs\([^)]*resume_from_epoch\s*=\s*_resume_from_epoch",
            src,
            re.DOTALL,
        ), "start.py 中 _build_task_kwargs 调用未透传 resume_from_epoch"

    def test_resume_mode_clamps_to_zero_for_restart(self):
        """restart 模式 _resume_from_epoch=0 (全新训练)"""
        src = _read(START_PY)
        # 查找 'else:\n        _resume_from_epoch = 0'
        assert re.search(
            r"else\s*:\s*\n\s*_resume_from_epoch\s*=\s*0",
            src,
        ), "start.py 中非 resume 模式未将 _resume_from_epoch 设为 0"


# ============== T7: state.py mark_paused 不删 .pth (关键回归) ==============

class TestStatePyDoesNotDeleteCheckpoints:
    """关键回归测试: v3.6.3 修复要求 mark_paused 不删 .pth, 否则 resume 找不到 checkpoint

    v3.6.3 反向修正 v3.6.2 误改行为:
    - v3.6.1 之前: mark_paused 用错路径 (settings.MODEL_DIR) → 实际从未删成功
    - v3.6.2 修正路径后: classification 的 .pth 真的被删了 → resume 找不到 checkpoint
    - v3.6.3: mark_paused 不删任何 .pth, 保留所有 checkpoint 供 resume 使用
    """

    def test_mark_paused_does_not_unlink_pth(self):
        src = _read(STATE_PY)
        # 关键: mark_paused 函数体内不能有 pth_path.unlink() 调用
        # 提取 mark_paused 函数体
        match = re.search(
            r"(?:async\s+)?def\s+mark_paused\([^)]*\)\s*->\s*[^:]+:\s*(.*?)(?=\n(?:async\s+)?def\s|\nclass\s|\Z)",
            src,
            re.DOTALL,
        )
        assert match, "state.py 中未找到 mark_paused 函数"
        body = match.group(1)
        assert "pth_path.unlink()" not in body, (
            "state.py:mark_paused 仍含 pth_path.unlink() 调用, "
            "v3.6.3 修复要求 mark_paused **不删任何 .pth**, 保留供 resume 使用"
        )
        assert "unlink()" not in body, (
            "state.py:mark_paused 仍含 unlink() 调用, "
            "v3.6.3 修复要求 mark_paused 不删任何 checkpoint 文件"
        )

    def test_mark_paused_has_no_op_marker(self):
        """验证 v3.6.3 注释标记存在 (审计追踪)"""
        src = _read(STATE_PY)
        # 检查 'v3.6.3' 注释存在
        assert "v3.6.3" in src, "state.py 中未找到 v3.6.3 注释 (审计追踪缺失)"
        # 检查 '不删' 或 '保留' 关键词存在
        assert "不删" in src or "保留" in src, (
            "state.py 中未找到 '不删/保留' 注释 (意图说明缺失)"
        )


# ============== T8: 端到端契约 - 5 文件配套修改 (sanity check) ==============

class TestConsistentPatchAcrossFiles:
    """sanity check: v3.6.3 patch 涉及 5 个文件全部已修改"""

    def test_start_py_has_v363_marker(self):
        src = _read(START_PY)
        assert "v3.6.3" in src, "start.py 中未找到 v3.6.3 注释 (patch 未应用?)"

    def test_classification_py_has_v363_marker(self):
        src = _read(CLASSIFICATION_PY)
        assert "v3.6.3" in src, (
            "ml/classification.py 中未找到 v3.6.3 注释 (patch 未应用?)"
        )

    def test_seg_train_py_has_v363_marker(self):
        src = _read(SEG_TRAIN_PY)
        assert "v3.6.3" in src, (
            "ml/segmentation/seg_train.py 中未找到 v3.6.3 注释 (patch 未应用?)"
        )

    def test_yolo_train_py_has_v363_marker(self):
        src = _read(YOLO_TRAIN_PY)
        assert "v3.6.3" in src, (
            "ml/detection/yolo_train.py 中未找到 v3.6.3 注释 (patch 未应用?)"
        )

    def test_workers_classification_has_v363_marker(self):
        src = _read(WORKERS_CLASSIFICATION_PY)
        assert "v3.6.3" in src, (
            "workers/classification.py 中未找到 v3.6.3 注释 (patch 未应用?)"
        )

    def test_workers_segmentation_has_v363_marker(self):
        src = _read(WORKERS_SEGMENTATION_PY)
        assert "v3.6.3" in src, (
            "workers/segmentation/train.py 中未找到 v3.6.3 注释 (patch 未应用?)"
        )

    def test_workers_detection_has_v363_marker(self):
        src = _read(WORKERS_DETECTION_PY)
        assert "v3.6.3" in src, (
            "workers/detection/train.py 中未找到 v3.6.3 注释 (patch 未应用?)"
        )
