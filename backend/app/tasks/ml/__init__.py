"""Tasks ML Package — 机器学习业务逻辑 (Stage 2.6 迁移)

按任务类型拆分子模块:
- classification.py: 图像分类 (timm)
- detection.py:      目标检测 (ultralytics YOLO)
- segmentation.py:   图像分割 (torchvision / SMP)

Stage 2.6 后会从 app/ml/{train,detection_train,segmentation_train}.py 迁移至此.
"""
# 兼容垫片占位
__all__: list[str] = []
