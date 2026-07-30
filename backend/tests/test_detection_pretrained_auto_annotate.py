"""
验证 v2.3.2 新增的 /api/detection/auto-annotate-pretrained 端点:
- 路由注册
- model_name regex (只允许 yolov8n/s/m/l/x)
- PREDEFINED_YOLO_MODELS 集合
- ultralytics 加载预训练权重
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def test_pretrained_route_registered():
    from app.tasks.api.detection import router  # noqa: PLC0415
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/auto-annotate-pretrained" in paths, (
        f"missing /auto-annotate-pretrained, have: {paths}"
    )


def test_predefined_models_constant():
    from app.tasks.workers.detection import PREDEFINED_YOLO_MODELS  # noqa: PLC0415
    assert PREDEFINED_YOLO_MODELS == {"yolov8n", "yolov8s", "yolov8m", "yolov8l", "yolov8x"}


def test_pretrained_task_signature():
    """auto_annotate_pretrained_task 应有 model_name / conf_threshold / iou_threshold 等参数"""
    from app.tasks.workers.detection import auto_annotate_pretrained_task  # noqa: PLC0415
    # Celery task 用 .run(...) 调用, 从 .run 参数推断
    import inspect
    sig = inspect.signature(auto_annotate_pretrained_task.run)
    params = list(sig.parameters.keys())
    assert "model_name" in params
    assert "dataset_id" in params
    assert "conf_threshold" in params
    assert "iou_threshold" in params


def test_pretrained_task_rejects_unknown_model():
    """不支持的 model_name 应快速返回 FAILURE, 不抛错"""
    from app.tasks.workers.detection import auto_annotate_pretrained_task  # noqa: PLC0415
    res = auto_annotate_pretrained_task.run(
        dataset_id=999, user_id=1, model_name="yolov9x",  # 不支持
    )
    assert res["status"] == "FAILURE"
    assert "不支持" in res["error"]


def test_coco_class_names_set_nonempty():
    from app.tasks.api.detection import _get_coco_class_names  # noqa: PLC0415
    names = _get_coco_class_names()
    assert isinstance(names, set)
    assert "person" in names  # COCO 必有
    assert "car" in names
    # 80 类左右
    assert 50 <= len(names) <= 100
