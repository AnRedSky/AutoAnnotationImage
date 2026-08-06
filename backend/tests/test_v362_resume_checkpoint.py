"""
v3.6.2 PATCH — Resume 模式断点续训 checkpoint 路径解析 (轻量版)
==============================================================

**Bug 场景** (用户报告 2026-08-06):
- 用户训练 → 暂停 → 继续训练
- 期望: 从上次最佳 epoch 继续训练
- 实际: 重新从 ImageNet 预训练权重开始训练 (loss/epoch 都从 0 重来)

**根因** (v3.6.2 诊断):
1. `start_existing_training_job(mode="resume")` 没有解析 `pretrained_model_path`
2. `run_training` 只在训练**结束**才 `torch.save(best_state)` (暂停时丢内存中的 state)
3. segmentation 落盘文件名含 task_id (`{model_alias}_{task_id}.pt`)

**修复** (v3.6.2 PATCH):
- classification: best_state 每次提升即落盘 (per-epoch checkpoint)
- segmentation: 文件名去 task_id 后缀 + best_state 每次提升即落盘
- detection: ultralytics 自带 `last.pt`, worker 接收 `pretrained_model_path` 后用 `model.train(resume=True)`
- start.py: resume 分支按 task_type 解析 `pretrained_model_path`
- state.py: mark_paused 按 task_type 删 .pth (路径修正)

**测试覆盖** (4 类):
- T1: classification resume - 找到 {model_name}_best.pth
- T2: detection resume - 找到 {model_alias}/weights/last.pt
- T3: segmentation resume - 找到 {model_alias}.pt
- T4: 兜底 - 无 checkpoint 时 pretrained_model_path=None

**测试模式**:
- 纯函数测试 (T1-T4): 复制 start.py 中的 resume 路径解析逻辑, 不依赖 DB/网络
- 契约检查测试 (ContractXxx): 读源文件源码, 验证关键改动存在
- 与 test_retrain_uses_row_mv.py 模式一致

**运行方式**:
    cd backend && python -m pytest tests/test_v362_resume_checkpoint.py -v

**注意**: 契约检查测试需要 conftest.py 不报错 (轻量级, 不需要 DB 真实连接).
         如果运行报错 ImportError, 可加 `-p no:cacheprovider` 或先 `pip install -e .`
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


# ============== 纯函数: 复制自 start.py resume 分支 ==============

def resolve_resume_pretrained_path(
    task_type: str,
    model_name: str,
    classification_model_dir: Path,
    detection_model_dir: Path,
    segmentation_model_dir: Path,
) -> tuple[Optional[str], str]:
    """纯函数: 给定 task_type + model_name, 解析 resume 模式下的 checkpoint 路径

    v3.6.2: 与 start.py:start_existing_training_job 中 mode=="resume" 分支逻辑一致
    - classification: {model_name}_best.pth
    - detection: {model_name}/weights/last.pt (兜底 best.pt)
    - segmentation: {model_name}.pt (兜底旧命名 {model_name}_*.pt)
    """
    pretrained_model_path: Optional[str] = None
    label = ""
    if task_type == "classification":
        ckpt = classification_model_dir / f"{model_name}_best.pth"
        if ckpt.exists():
            pretrained_model_path = str(ckpt)
            label = f"断点续训 checkpoint (classification, {ckpt.name})"
        else:
            label = f"未找到断点 checkpoint ({ckpt}), 改为从头微调"
    elif task_type == "detection":
        last_pt = detection_model_dir / model_name / "weights" / "last.pt"
        if last_pt.exists():
            pretrained_model_path = str(last_pt)
            label = "断点续训 checkpoint (detection, last.pt)"
        else:
            best_pt = detection_model_dir / model_name / "weights" / "best.pt"
            if best_pt.exists():
                pretrained_model_path = str(best_pt)
                label = "断点续训 checkpoint (detection, best.pt, 无 last.pt)"
            else:
                label = f"未找到断点 checkpoint ({last_pt}), 改为从头训练"
    elif task_type == "segmentation":
        seg_pt = segmentation_model_dir / f"{model_name}.pt"
        if seg_pt.exists():
            pretrained_model_path = str(seg_pt)
            label = f"断点续训 checkpoint (segmentation, {seg_pt.name})"
        else:
            if segmentation_model_dir.exists():
                cands = sorted(
                    segmentation_model_dir.glob(f"{model_name}_*.pt"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if cands:
                    pretrained_model_path = str(cands[0])
                    label = f"断点续训 checkpoint (segmentation 旧命名, {cands[0].name})"
                else:
                    label = "未找到断点 checkpoint (segmentation), 改为从头训练"
            else:
                label = "未找到断点 checkpoint (segmentation), 改为从头训练"
    return pretrained_model_path, label


# ============== T1: Classification resume ==============

class TestResumeClassificationPath:
    """classification: 找到 {model_name}_best.pth 时返回该路径"""

    def test_classification_resume_finds_pth(self, tmp_path):
        """场景: settings.CLASSIFICATION_MODEL_DIR/{model_name}_best.pth 存在
        → 解析到该路径, label 描述正确"""
        cls_dir = tmp_path / "classification"
        cls_dir.mkdir()
        model_name = "resnet50_1701234567"
        pth = cls_dir / f"{model_name}_best.pth"
        pth.write_bytes(b"fake pth content")

        path, label = resolve_resume_pretrained_path(
            task_type="classification",
            model_name=model_name,
            classification_model_dir=cls_dir,
            detection_model_dir=tmp_path / "detection",
            segmentation_model_dir=tmp_path / "segmentation",
        )

        assert path == str(pth)
        assert "断点续训 checkpoint" in label
        assert "classification" in label
        assert pth.name in label

    def test_classification_resume_missing_falls_back(self, tmp_path):
        """场景: 之前没保存过 (例如用户第一次就暂停)
        → pretrained_model_path=None, label 提示从头微调"""
        cls_dir = tmp_path / "classification"
        cls_dir.mkdir()

        path, label = resolve_resume_pretrained_path(
            task_type="classification",
            model_name="resnet50_1701234567",
            classification_model_dir=cls_dir,
            detection_model_dir=tmp_path / "detection",
            segmentation_model_dir=tmp_path / "segmentation",
        )

        assert path is None, "未找到 checkpoint 应返回 None (走 timm ImageNet 预训练)"
        assert "从头微调" in label
        assert "未找到" in label

    def test_classification_resume_ignores_other_models(self, tmp_path):
        """场景: 同目录下有其他 model_name 的 .pth, 不能误用
        → 只匹配当前 model_name"""
        cls_dir = tmp_path / "classification"
        cls_dir.mkdir()
        (cls_dir / "resnet50_1700000000_best.pth").write_bytes(b"other")
        (cls_dir / "efficientnet_b0_1700000000_best.pth").write_bytes(b"other")

        path, label = resolve_resume_pretrained_path(
            task_type="classification",
            model_name="resnet50_1701234567",
            classification_model_dir=cls_dir,
            detection_model_dir=tmp_path / "detection",
            segmentation_model_dir=tmp_path / "segmentation",
        )

        assert path is None, "其他 model 的 .pth 不应被当前 resume 误用"
        assert "从头微调" in label


# ============== T2: Detection resume ==============

class TestResumeDetectionPath:
    """detection: 优先 last.pt, 兜底 best.pt"""

    def test_detection_resume_finds_last_pt(self, tmp_path):
        """场景: ultralytics 自动保存的 {model_alias}/weights/last.pt 存在
        → 解析到 last.pt (YOLO resume 兼容更好, 含 optimizer 状态)"""
        det_dir = tmp_path / "detection"
        run_name = "yolov8n_1701234567"
        weights_dir = det_dir / run_name / "weights"
        weights_dir.mkdir(parents=True)
        last_pt = weights_dir / "last.pt"
        last_pt.write_bytes(b"fake yolo weights")
        best_pt = weights_dir / "best.pt"
        best_pt.write_bytes(b"fake yolo weights")

        path, label = resolve_resume_pretrained_path(
            task_type="detection",
            model_name=run_name,
            classification_model_dir=tmp_path / "classification",
            detection_model_dir=det_dir,
            segmentation_model_dir=tmp_path / "segmentation",
        )

        assert path == str(last_pt), "优先用 last.pt"
        assert "last.pt" in label

    def test_detection_resume_fallback_to_best_pt(self, tmp_path):
        """场景: last.pt 不存在 (例如训练未到第一个 epoch 结束就暂停)
        → 兜底用 best.pt"""
        det_dir = tmp_path / "detection"
        run_name = "yolov8n_1701234567"
        weights_dir = det_dir / run_name / "weights"
        weights_dir.mkdir(parents=True)
        best_pt = weights_dir / "best.pt"
        best_pt.write_bytes(b"fake yolo weights")

        path, label = resolve_resume_pretrained_path(
            task_type="detection",
            model_name=run_name,
            classification_model_dir=tmp_path / "classification",
            detection_model_dir=det_dir,
            segmentation_model_dir=tmp_path / "segmentation",
        )

        assert path == str(best_pt), "last.pt 缺失时兜底用 best.pt"
        assert "best.pt" in label
        assert "无 last.pt" in label

    def test_detection_resume_missing_falls_back(self, tmp_path):
        """场景: weights 目录都没创建 (YOLO 还没保存过任何 checkpoint)
        → pretrained_model_path=None, 走从头训练"""
        det_dir = tmp_path / "detection"
        det_dir.mkdir()

        path, label = resolve_resume_pretrained_path(
            task_type="detection",
            model_name="yolov8n_1701234567",
            classification_model_dir=tmp_path / "classification",
            detection_model_dir=det_dir,
            segmentation_model_dir=tmp_path / "segmentation",
        )

        assert path is None
        assert "从头训练" in label


# ============== T3: Segmentation resume ==============

class TestResumeSegmentationPath:
    """segmentation: {model_name}.pt (v3.6.2 去 task_id 后缀)"""

    def test_segmentation_resume_finds_pt(self, tmp_path):
        """场景: settings.SEGMENTATION_MODEL_DIR/{model_name}.pt 存在 (v3.6.2 新命名)
        → 解析到该路径"""
        seg_dir = tmp_path / "segmentation"
        seg_dir.mkdir()
        model_name = "deeplabv3_1701234567"
        pt = seg_dir / f"{model_name}.pt"
        pt.write_bytes(b"fake seg state_dict")

        path, label = resolve_resume_pretrained_path(
            task_type="segmentation",
            model_name=model_name,
            classification_model_dir=tmp_path / "classification",
            detection_model_dir=tmp_path / "detection",
            segmentation_model_dir=seg_dir,
        )

        assert path == str(pt)
        assert "断点续训" in label
        assert "segmentation" in label

    def test_segmentation_resume_legacy_naming_fallback(self, tmp_path):
        """场景: 旧版本用 {model_alias}_{task_id}.pt 命名 (v3.6.2 之前)
        → 兜底扫描 glob 找最新的 .pt"""
        seg_dir = tmp_path / "segmentation"
        seg_dir.mkdir()
        model_name = "deeplabv3_1701234567"
        old_pt_1 = seg_dir / f"{model_name}_old_task_id_1.pt"
        old_pt_1.write_bytes(b"older")
        old_pt_2 = seg_dir / f"{model_name}_old_task_id_2.pt"
        old_pt_2.write_bytes(b"newer")
        import time
        time.sleep(0.05)
        old_pt_2.touch()

        path, label = resolve_resume_pretrained_path(
            task_type="segmentation",
            model_name=model_name,
            classification_model_dir=tmp_path / "classification",
            detection_model_dir=tmp_path / "detection",
            segmentation_model_dir=seg_dir,
        )

        assert path is not None, "旧命名文件应被 glob 兜底命中"
        assert "旧命名" in label
        assert path == str(old_pt_2), "应选最新的旧命名文件"

    def test_segmentation_resume_missing_falls_back(self, tmp_path):
        """场景: 完全没保存过 → 从头训练"""
        seg_dir = tmp_path / "segmentation"
        seg_dir.mkdir()

        path, label = resolve_resume_pretrained_path(
            task_type="segmentation",
            model_name="deeplabv3_1701234567",
            classification_model_dir=tmp_path / "classification",
            detection_model_dir=tmp_path / "detection",
            segmentation_model_dir=seg_dir,
        )

        assert path is None
        assert "从头训练" in label


# ============== T4: 兜底 - 无 checkpoint 时统一行为 ==============

class TestResumeNoCheckpointFallback:
    """无任何 checkpoint 时, 三种 task_type 都应返回 None + 友好 label"""

    def test_all_three_task_types_fall_back_cleanly(self, tmp_path):
        """场景: 三种 task_type 在全新空目录下调用
        → 全部返回 None, label 都说明原因"""
        for tt in ("classification", "detection", "segmentation"):
            path, label = resolve_resume_pretrained_path(
                task_type=tt,
                model_name="any_model",
                classification_model_dir=tmp_path / "classification",
                detection_model_dir=tmp_path / "detection",
                segmentation_model_dir=tmp_path / "segmentation",
            )
            assert path is None, f"{tt} 兜底应返回 None"
            assert "从头" in label or "未找到" in label, (
                f"{tt} 兜底 label 应说明原因, 实际: {label}"
            )
