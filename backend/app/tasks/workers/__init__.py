"""Tasks Workers Package — Celery 任务入口 (Stage 2.6 迁移)

按任务类型拆分子模块:
- training.py:     分类训练任务入口
- detection.py:    检测训练任务入口
- segmentation.py: 分割训练任务入口

每个文件薄 (委托 app.tasks.service.TrainingLifecycleService),
不写具体训练逻辑.
"""
# 兼容垫片占位
__all__: list[str] = []
