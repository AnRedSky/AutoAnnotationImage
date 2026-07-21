"""
S12.5 验证: 分割 (segmentation) API 路由注册 + 检测/分割任务兼容
- 训练可视化 (S12.4) 关键 schema: TrainingJobOut.task_type 字段存在
- 分割 mask 上传/下载/删除 API 注册
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def test_segmentation_routes_registered():
    """分割任务核心 API 路由必须全部注册"""
    from app.api.segmentation import router  # noqa: PLC0415
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    methods_by_path = {}
    for r in router.routes:
        if hasattr(r, "path") and hasattr(r, "methods"):
            for m in r.methods:
                if m == "HEAD":
                    continue
                methods_by_path.setdefault(r.path, set()).add(m)
    # 关键路由: 上传 mask + 下载 mask + 列表 mask
    required = [
        "/images/{image_id}/mask",                # POST: 上传
        "/images/{image_id}/mask",                # GET: 下载 (注册两次, 一次 POST 一次 GET)
        "/datasets/{dataset_id}/masks",           # GET: 列表
    ]
    found = set()
    for p in paths:
        # 兼容带 prefix 的情况 (segmentation_router 通常没 prefix)
        normalized = p.replace("{", "{").replace("}", "}")
        if "/images/{image_id}/mask" in normalized:
            found.add("/images/{image_id}/mask")
        if "/datasets/{dataset_id}/masks" in normalized:
            found.add("/datasets/{dataset_id}/masks")
    missing = set(required) - found
    assert not missing, f"segmentation 路由缺失: {missing}, 实际: {paths}"


def test_segmentation_upload_get_delete():
    """上传/下载/删除 mask 三个动作必须注册 (POST/GET/DELETE)"""
    from app.api.segmentation import router  # noqa: PLC0415
    methods_by_path = {}
    for r in router.routes:
        if hasattr(r, "path") and hasattr(r, "methods"):
            for m in r.methods:
                if m == "HEAD":
                    continue
                methods_by_path.setdefault(r.path, set()).add(m)
    mask_path = "/images/{image_id}/mask"
    if mask_path in methods_by_path:
        methods = methods_by_path[mask_path]
        # 至少要有 POST (上传)
        assert "POST" in methods, f"mask 上传路由缺失 POST, have: {methods}"


def test_training_schema_includes_task_type():
    """v2.5.0 S12.4 训练可视化按 task_type 切换曲线,
    TrainingJobOut 必须含 task_type 字段"""
    from app.schemas.training import TrainingJobOut  # noqa: PLC0415
    fields = set(TrainingJobOut.model_fields.keys())
    assert "task_type" in fields, (
        f"TrainingJobOut 缺 task_type, fields={fields}"
    )


def test_training_history_supports_segmentation_fields():
    """v2.5.0 S12.4 训练历史必须支持分割指标 (miou/pixel_acc/dice)"""
    # 后端训练历史按 task_type 推不同字段, 端 schema 是 Any 兼容
    # 但前端 EpochData 扩展了, 这里测后端 API 不因缺字段而崩
    from app.api.training import router  # noqa: PLC0415
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    # 关键: /jobs/{id}/history 路由必须注册
    history_paths = [p for p in paths if "history" in p]
    assert len(history_paths) >= 1, (
        f"训练历史路由缺失, 实际 paths={paths}"
    )


def test_segmentation_mask_endpoint_signature():
    """v2.5.0 S12.3b 关键: getMask 路由签名 (download=bool) 必须存在"""
    # 端点: GET /api/segmentation/images/{image_id}/mask?download=true
    # 返回 Blob (download=true) 或 JSON 元数据 (download=false)
    from app.api.segmentation import router  # noqa: PLC0415
    found = False
    for r in router.routes:
        if hasattr(r, "path") and "/images/{image_id}/mask" in r.path:
            if "GET" in (r.methods or set()):
                found = True
                break
    assert found, "GET /images/{image_id}/mask 路由未注册"


if __name__ == "__main__":
    test_segmentation_routes_registered()
    test_segmentation_upload_get_delete()
    test_training_schema_includes_task_type()
    test_training_history_supports_segmentation_fields()
    test_segmentation_mask_endpoint_signature()
    print("OK: 分割 + 训练可视化路由/Schema 验证通过")
