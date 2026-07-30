"""验证: 修复后无 Pydantic UserWarning (Field has conflict with protected namespace)"""
import sys
import warnings
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def test_schema_no_protected_namespace_warning():
    """含 model_name/model_id 字段的 Pydantic schema 实例化时不应再抛 UserWarning"""
    # 抓 UserWarning, 任何一条都让测试失败
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        from app.schemas.training import (  # noqa: PLC0415
            TrainStartRequest, TrainingJobUpdate,
        )
        from app.schemas.segmentation import SegmentationTrainRequest  # noqa: PLC0415
        from app.schemas.detection import DetectionTrainRequest  # noqa: PLC0415
        from app.tasks.api.auto_annotate import AutoAnnotateRequest  # noqa: PLC0415
        from app.tasks.api.preview import PreviewConfidenceRequest  # noqa: PLC0415

        # 实例化每个有 model_name 的 schema
        TrainStartRequest(dataset_id=1, model_name="v1")
        TrainingJobUpdate(model_name="v1")
        SegmentationTrainRequest(dataset_id=1, model_name="seg_v1")
        DetectionTrainRequest(dataset_id=1, model_name="det_v1")
        AutoAnnotateRequest(dataset_id=1, model_name="eff")
        # v2.1.7 漏掉: image.py:32 PreviewConfidenceRequest
        PreviewConfidenceRequest(
            dataset_id=1, image_ids=[1],
            model_name="efficientnet_b0", model_id=None,
            confidence_threshold=0.6, use_finetune=True,
        )

    # 收集所有 "protected namespace" 警告
    bad = [
        str(x.message) for x in w
        if "protected namespace" in str(x.message)
        or "Field " in str(x.message) and "model_" in str(x.message)
    ]
    assert not bad, f"残留 UserWarning: {bad}"


if __name__ == "__main__":
    test_schema_no_protected_namespace_warning()
    print("OK: no protected namespace warning")
