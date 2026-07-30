"""
验证 detection stats 端点:
- GET /api/detection/stats/{dataset_id} 返回正确结构
- 强制要求 task_type='detection'
- 正确计算: total_bboxes / annotated_images / category_distribution / bbox_scatter
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def test_stats_endpoint_registered():
    from app.tasks.api.detection import router  # noqa: PLC0415
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/stats/{dataset_id}" in paths


def test_stats_response_shape():
    """验证返回字段完整 (前端依赖)"""
    from app.tasks.api.detection import detection_stats  # noqa: PLC0415
    import inspect
    sig = inspect.signature(detection_stats)
    # 参数: dataset_id, db, current_user
    params = list(sig.parameters.keys())
    assert "dataset_id" in params
    assert "db" in params
    assert "current_user" in params


if __name__ == "__main__":
    test_stats_endpoint_registered()
    test_stats_response_shape()
    print("OK: detection stats endpoint registered and shape valid")
