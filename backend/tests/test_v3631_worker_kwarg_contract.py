"""
v3.6.3.1 HOTFIX — Worker → ML 函数参数名契约测试
====================================================

**Bug 场景 (用户报告 2026-08-06)**:
- v3.6.3 patch 修改了 `workers/classification.py:220` 增加 resume 续训参数
- 但写错了 kwarg 名: `resume_from_epoch=resume_from_epoch`
- `run_training` 实际签名是 `start_epoch=`, 不是 `resume_from_epoch=`
- 用户点击「继续训练」时 worker 启动 → 训练时崩:
  `TypeError: run_training() got an unexpected keyword argument 'resume_from_epoch'`

**根因**:
- 业务层 (Celery task) 参数名: `resume_from_epoch` (强调是 resume 场景的起点)
- ML 层 (run_training / train_segmentation / train_yolo) 参数名: `start_epoch` (强调是训练循环起点)
- 透传时需要做命名翻译, v3.6.3 patch 漏了这一步
- segmentation/detection worker 写对了 (`start_epoch=resume_from_epoch`)
- classification worker 写错了 (`resume_from_epoch=resume_from_epoch`)

**修复 (v3.6.3.1)**:
- workers/classification.py:220: `resume_from_epoch=...` → `start_epoch=resume_from_epoch`
- 配套契约测试: 自动从 worker 源码 AST 提取 kwarg, 与 ML 函数 inspect 签名比对

**测试覆盖**:
- T1: classification worker 调 run_training 的所有 kwarg 都在签名中
- T2: segmentation worker 调 train_segmentation 的所有 kwarg 都在签名中
- T3: detection worker 调 train_yolo 的所有 kwarg 都在签名中
- T4: 显式断言 resume_from_epoch 已正确翻译为 start_epoch (防回归)

**测试模式**:
- 静态 AST 扫描: 不依赖 DB/Celery/Redis, 纯文本解析
- 防御性: 任何 worker 调 ML 函数时 kwarg 名错都会立即失败
- 与 test_v362_resume_checkpoint.py 模式一致

**运行方式**:
    cd backend && python -m pytest tests/test_v3631_worker_kwarg_contract.py -v
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Dict, Set, Tuple

import pytest


# ============== 静态分析工具 ==============

def _extract_call_kwargs(source_path: Path, func_name: str) -> Set[str]:
    """从 Python 源文件中提取指定函数体内对某函数调用的所有 kwargs

    Args:
        source_path: 源文件路径
        func_name: 目标函数名 (例如 "run_training")

    Returns:
        kwargs 名称集合
    """
    src = source_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(source_path))

    # 在所有函数定义中查找名为 func_name 的调用
    kwargs_found: Set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        # 遍历函数体内所有调用
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                # 匹配的几种形式: run_training(...), self.run_training(...)
                target_name = None
                if isinstance(sub.func, ast.Name):
                    target_name = sub.func.id
                elif isinstance(sub.func, ast.Attribute):
                    target_name = sub.func.attr

                if target_name == func_name:
                    for kw in sub.keywords:
                        kwargs_found.add(kw.arg)

    return kwargs_found


def _get_function_param_names(func) -> Set[str]:
    """用 inspect 提取函数的所有参数名"""
    sig = inspect.signature(func)
    return set(sig.parameters.keys())


def _get_task_kwargs(source_path: Path, task_func_name: str) -> Set[str]:
    """提取 Celery task 函数签名 (def train_model_task(self, ...)) 的所有参数

    用于测试 worker 接收的参数名是否符合预期
    """
    src = source_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(source_path))

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == task_func_name:
            return {arg.arg for arg in node.args.args if arg.arg != "self"}
    return set()


# ============== 路径常量 ==============

BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKERS_DIR = BACKEND_ROOT / "app" / "tasks" / "workers"
ML_DIR = BACKEND_ROOT / "app" / "tasks" / "ml"

CLASSIFICATION_WORKER = WORKERS_DIR / "classification.py"
SEGMENTATION_WORKER = WORKERS_DIR / "segmentation" / "train.py"
DETECTION_WORKER = WORKERS_DIR / "detection" / "train.py"

CLASSIFICATION_ML = ML_DIR / "classification.py"
SEGMENTATION_ML = ML_DIR / "segmentation" / "seg_train.py"
DETECTION_ML = ML_DIR / "detection" / "yolo_train.py"


# ============== T1: Classification worker → run_training 契约 ==============

class TestClassificationWorkerContract:
    """workers/classification.py 中所有对 run_training 的调用, kwargs 必须与函数签名一致"""

    def test_run_training_kwargs_all_valid(self):
        """静态扫描: worker 中所有对 run_training(...) 的 kwargs 都必须是函数签名中的参数"""
        # 1) 提取 worker 调 run_training 的所有 kwarg
        worker_kwargs = _extract_call_kwargs(CLASSIFICATION_WORKER, "run_training")
        assert worker_kwargs, "应至少找到一个 run_training 调用"

        # 2) 提取 run_training 真实签名
        from app.tasks.ml.classification import run_training
        func_params = _get_function_param_names(run_training)

        # 3) 断言: worker_kwargs ⊆ func_params
        invalid = worker_kwargs - func_params
        assert not invalid, (
            f"workers/classification.py 调用 run_training(...) 时使用了非法 kwarg: {invalid}\n"
            f"  worker 实际传的 kwargs: {sorted(worker_kwargs)}\n"
            f"  run_training 函数签名: {sorted(func_params)}\n"
            f"  v3.6.3.1 HOTFIX: 历史上因 resume_from_epoch 写错 (应为 start_epoch) 崩过"
        )

    def test_resume_from_epoch_translated_to_start_epoch(self):
        """显式断言: worker 入参 resume_from_epoch 必须翻译为 ML 层 start_epoch (防回归)

        v3.6.3 bug: worker 直接 `resume_from_epoch=resume_from_epoch` → TypeError
        v3.6.3.1 fix: worker 改为 `start_epoch=resume_from_epoch`
        """
        worker_kwargs = _extract_call_kwargs(CLASSIFICATION_WORKER, "run_training")

        # 1) 关键修复: worker 不能直接传 resume_from_epoch 给 run_training
        assert "resume_from_epoch" not in worker_kwargs, (
            "v3.6.3.1 HOTFIX 回归: workers/classification.py 不应直接传 "
            "resume_from_epoch=... 给 run_training (会触发 TypeError)。"
            "请改为 start_epoch=resume_from_epoch, 与 segmentation/detection 对齐。"
        )

        # 2) 必须显式传 start_epoch
        assert "start_epoch" in worker_kwargs, (
            "v3.6.3.1 HOTFIX 回归: 缺少 start_epoch=... 透传。"
            "断点续训的 epoch 起点必须传到 ML 层 run_training。"
        )

    def test_worker_task_accepts_resume_from_epoch(self):
        """worker task 入口仍接受 resume_from_epoch (业务层语义)

        - Celery task 入参: resume_from_epoch (业务命名, 强调 resume 场景)
        - ML 层: start_epoch (内部命名, 强调训练循环起点)
        - 翻译发生在 worker 内部 (业务层 → ML 层)
        """
        task_kwargs = _get_task_kwargs(CLASSIFICATION_WORKER, "train_model_task")
        assert "resume_from_epoch" in task_kwargs, (
            "train_model_task 任务签名应保留 resume_from_epoch 入参, "
            "供 Celery 任务透传到 ML 层 (start_epoch)。"
        )
        assert "pretrained_model_path" in task_kwargs, (
            "train_model_task 应保留 pretrained_model_path 入参, "
            "v3.6.2 引入, v3.6.3 仍在用。"
        )


# ============== T2: Segmentation worker → train_segmentation 契约 ==============

class TestSegmentationWorkerContract:
    """workers/segmentation/train.py 中所有对 train_segmentation 的调用, kwargs 必须与函数签名一致"""

    def test_train_segmentation_kwargs_all_valid(self):
        """静态扫描: worker 中所有对 train_segmentation(...) 的 kwargs 都必须是函数签名中的参数"""
        worker_kwargs = _extract_call_kwargs(SEGMENTATION_WORKER, "train_segmentation")
        assert worker_kwargs, "应至少找到一个 train_segmentation 调用"

        from app.tasks.ml.segmentation.seg_train import train_segmentation
        func_params = _get_function_param_names(train_segmentation)

        invalid = worker_kwargs - func_params
        assert not invalid, (
            f"workers/segmentation/train.py 调用 train_segmentation(...) 时使用了非法 kwarg: {invalid}\n"
            f"  worker 实际传的 kwargs: {sorted(worker_kwargs)}\n"
            f"  train_segmentation 函数签名: {sorted(func_params)}\n"
            f"  v3.6.3.1 HOTFIX: 防御性检查, 与 classification 对齐"
        )

    def test_resume_from_epoch_translated_to_start_epoch(self):
        """显式断言: segmentation worker 入参 resume_from_epoch 必须翻译为 ML 层 start_epoch"""
        worker_kwargs = _extract_call_kwargs(SEGMENTATION_WORKER, "train_segmentation")

        assert "resume_from_epoch" not in worker_kwargs, (
            "v3.6.3.1 HOTFIX 回归: workers/segmentation/train.py 不应直接传 "
            "resume_from_epoch=... 给 train_segmentation (会触发 TypeError)。"
            "请改为 start_epoch=resume_from_epoch。"
        )

        assert "start_epoch" in worker_kwargs, (
            "v3.6.3.1 HOTFIX 回归: 缺少 start_epoch=... 透传。"
        )


# ============== T3: Detection worker → train_yolo 契约 ==============

class TestDetectionWorkerContract:
    """workers/detection/train.py 中所有对 train_yolo 的调用, kwargs 必须与函数签名一致"""

    def test_train_yolo_kwargs_all_valid(self):
        """静态扫描: worker 中所有对 train_yolo(...) 的 kwargs 都必须是函数签名中的参数"""
        worker_kwargs = _extract_call_kwargs(DETECTION_WORKER, "train_yolo")
        assert worker_kwargs, "应至少找到一个 train_yolo 调用"

        from app.tasks.ml.detection.yolo_train import train_yolo
        func_params = _get_function_param_names(train_yolo)

        invalid = worker_kwargs - func_params
        assert not invalid, (
            f"workers/detection/train.py 调用 train_yolo(...) 时使用了非法 kwarg: {invalid}\n"
            f"  worker 实际传的 kwargs: {sorted(worker_kwargs)}\n"
            f"  train_yolo 函数签名: {sorted(func_params)}\n"
            f"  v3.6.3.1 HOTFIX: 防御性检查, 与 classification 对齐"
        )

    def test_resume_from_epoch_translated_to_start_epoch(self):
        """显式断言: detection worker 入参 resume_from_epoch 必须翻译为 ML 层 start_epoch"""
        worker_kwargs = _extract_call_kwargs(DETECTION_WORKER, "train_yolo")

        assert "resume_from_epoch" not in worker_kwargs, (
            "v3.6.3.1 HOTFIX 回归: workers/detection/train.py 不应直接传 "
            "resume_from_epoch=... 给 train_yolo (会触发 TypeError)。"
            "请改为 start_epoch=resume_from_epoch。"
        )

        assert "start_epoch" in worker_kwargs, (
            "v3.6.3.1 HOTFIX 回归: 缺少 start_epoch=... 透传。"
        )


# ============== T4: 跨 worker 一致性 ==============

class TestCrossWorkerConsistency:
    """三个 worker 的 resume_from_epoch 处理必须一致 (同时存在或同时缺失)"""

    def test_all_workers_handle_resume_from_epoch_consistently(self):
        """三 worker 必须都用同一种方式处理 resume_from_epoch → start_epoch 翻译"""
        cls_kwargs = _extract_call_kwargs(CLASSIFICATION_WORKER, "run_training")
        seg_kwargs = _extract_call_kwargs(SEGMENTATION_WORKER, "train_segmentation")
        det_kwargs = _extract_call_kwargs(DETECTION_WORKER, "train_yolo")

        # 三个 worker 都应该:
        # 1) 不直接传 resume_from_epoch= 给 ML 层
        for name, kws in [("classification", cls_kwargs), ("segmentation", seg_kwargs), ("detection", det_kwargs)]:
            assert "resume_from_epoch" not in kws, (
                f"{name} worker 不应直接传 resume_from_epoch=... 给 ML 层, "
                f"应翻译为 start_epoch=resume_from_epoch"
            )

        # 2) 都应该传 start_epoch= 给 ML 层
        for name, kws in [("classification", cls_kwargs), ("segmentation", seg_kwargs), ("detection", det_kwargs)]:
            assert "start_epoch" in kws, (
                f"{name} worker 应传 start_epoch=... 给 ML 层"
            )

    def test_all_workers_pass_pretrained_model_path(self):
        """三 worker 都应该传 pretrained_model_path (v3.6.2 引入, 断点续训必需)"""
        cls_kwargs = _extract_call_kwargs(CLASSIFICATION_WORKER, "run_training")
        seg_kwargs = _extract_call_kwargs(SEGMENTATION_WORKER, "train_segmentation")
        det_kwargs = _extract_call_kwargs(DETECTION_WORKER, "train_yolo")

        for name, kws in [("classification", cls_kwargs), ("segmentation", seg_kwargs), ("detection", det_kwargs)]:
            assert "pretrained_model_path" in kws, (
                f"{name} worker 缺少 pretrained_model_path=... 透传 (v3.6.2 引入)"
            )
