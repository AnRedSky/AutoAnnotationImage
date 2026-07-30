"""
验证 detection API 路由注册:
- /detection/annotations/clear/{image_id} 必须优先于 /detection/annotations/{bbox_id}
  (避免 FastAPI 把 'clear' 解析成 bbox_id)
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def test_clear_bboxes_route_registered():
    from app.tasks.api.detection import router  # noqa: PLC0415
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    methods_by_path = {}
    for r in router.routes:
        if hasattr(r, "path") and hasattr(r, "methods"):
            for m in r.methods:
                if m == "HEAD":
                    continue
                methods_by_path.setdefault(r.path, set()).add(m)
    assert "/annotations/clear/{image_id}" in paths, (
        f"missing /annotations/clear/{{image_id}}, have: {paths}"
    )
    assert "DELETE" in methods_by_path["/annotations/clear/{image_id}"]


def test_clear_route_before_dynamic_route():
    """/annotations/clear/{image_id} 必须在 /annotations/{bbox_id} 之前声明,
    否则 FastAPI 会把 'clear' 解析成 bbox_id -> 触发 422 校验错误"""
    from app.tasks.api.detection import router  # noqa: PLC0415
    clear_idx = None
    dynamic_idx = None
    for idx, r in enumerate(router.routes):
        if not hasattr(r, "path"):
            continue
        if r.path == "/annotations/clear/{image_id}" and "DELETE" in r.methods:
            clear_idx = idx
        elif r.path == "/annotations/{bbox_id}" and "DELETE" in r.methods:
            dynamic_idx = idx
    assert clear_idx is not None, "missing clear route"
    assert dynamic_idx is not None, "missing dynamic route"
    assert clear_idx < dynamic_idx, (
        f"clear route (idx={clear_idx}) must be BEFORE "
        f"dynamic route (idx={dynamic_idx}), else FastAPI will match "
        f"'clear' as bbox_id"
    )


if __name__ == "__main__":
    test_clear_bboxes_route_registered()
    test_clear_route_before_dynamic_route()
    print("OK: clear route registered before dynamic bbox_id route")
