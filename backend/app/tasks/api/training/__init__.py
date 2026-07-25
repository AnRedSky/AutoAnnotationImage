"""
training API Package (v3.0.0 Phase O 拆分)
============================================

**职责**: 训练任务的启动/进度/历史/CRUD/控制/日志

**目录结构** (拆分自原 training.py, 1019 行 → 5 子模块):
- start.py      新建 + restart/resume (POST /start, POST /jobs/{id}/start)
- progress.py   进度查询 (REST 轮询) + SSE 实时推送
- history.py    训练历史曲线 (双源: Redis + DB)
- jobs.py       任务查询/详情/取消/暂停/编辑/删除/错误
- log.py        训练日志读取/追加

**v3.0.0 Phase O 拆分**:
- 从原 training.py (1019 行) 拆出 5 个子模块, 每个模块只关注单一职责
- __init__.py 负责拼装: 包含各子 router, 统一对外暴露
- 旧 training.py 文件已删除 (避免与本包同名冲突)

**对外接口 (完全向后兼容)**: 14 个路由保持不变
- POST   /api/training/start
- GET    /api/training/progress/{task_id}
- GET    /api/training/progress/stream/{task_id}     (SSE)
- GET    /api/training/history/{task_id}
- GET    /api/training/jobs                          (列表 + 分页)
- GET    /api/training/jobs/                         (兼容尾斜杠)
- GET    /api/training/jobs/{job_id}
- POST   /api/training/jobs/{job_id}/cancel
- POST   /api/training/jobs/{job_id}/pause
- POST   /api/training/jobs/{job_id}/start           (restart/resume)
- POST   /api/training/jobs/{job_id}/error
- GET    /api/training/jobs/{job_id}/log
- POST   /api/training/jobs/{job_id}/log
- PATCH  /api/training/jobs/{job_id}
- DELETE /api/training/jobs/{job_id}
"""
from fastapi import APIRouter

from app.tasks.api.training.start import router as start_router
from app.tasks.api.training.progress import router as progress_router
from app.tasks.api.training.history import router as history_router
from app.tasks.api.training.jobs import router as jobs_router
from app.tasks.api.training.log import router as log_router

# 拼装顶层 router
router = APIRouter()
router.include_router(start_router)
router.include_router(progress_router)
router.include_router(history_router)
router.include_router(jobs_router)
router.include_router(log_router)

# 命名导出 (供 tasks/__init__.py import)
training_router = router  # 兼容旧别名


__all__ = [
    "router",
    "training_router",
    "start_router",
    "progress_router",
    "history_router",
    "jobs_router",
    "log_router",
]
