"""
Segmentation ML Package (v2.0.0 图像分割)
==========================================

封装 torchvision 分割模型 (DeepLabV3+) 的训练/推理/数据加载.

- 不强制依赖 GPU, CPU 可跑 (适合中小规模验证场景)
- 训练函数接受 progress_cb 回调, 写 TrainingJob.progress
- 推理输出 PIL 索引图 (P-mode), 像素值 = 预测类别索引
"""
