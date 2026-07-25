"""
Timm Classification ML Backend Plugin
======================================

timm 框架的图像分类后端. Stage 4 第四个具体插件实现 (展示用).

**职责**:
- 把 `app.tasks.ml.classification` 中的 timm 模型构建/训练/推理包装为 PluginInterface
- 提供 `build_model` / `train` / `predict` 三个核心方法
- 业务代码通过 `PluginRegistry.get("ml_backend", "timm_classification")` 获取

**依赖方向**:
- plugin/ml_backends/timm_classification.py → app.tasks.ml.classification (复用现有 ML)
- plugin → 不直接依赖任何 app/* 业务代码 (除 ML 模块)

**当前状态**:
- 提供薄包装, 委托给 classification.py 的 `build_timm_model` / `train_classifier` / `predict_classifier`
- 真正的训练仍走 Celery 异步任务, 此插件主要用于 service 层调用 ML 能力的统一入口

**未来扩展**:
- UltralyticsBackend: YOLOv8 目标检测
- TorchvisionBackend: torchvision 语义分割
- HuggingFaceBackend: Transformers 多模态
"""
import logging
from typing import Any, Optional

from app.common.interfaces import PluginInterface
from app.registry import PluginRegistry

logger = logging.getLogger(__name__)


class TimmClassificationPlugin(PluginInterface):
    """timm 图像分类后端插件 (默认)

    包装 app.tasks.ml.classification 的核心函数, 抽象为 MLBackendInterface.
    主要服务于:
    1. AI service (预标注) — 走 predict()
    2. training service (训练) — 走 build_model() + train()
    3. model service (加载) — 走 build_model()

    Attributes:
        name: "timm_classification" (唯一)
        version: "1.0.0"
        category: "ml_backend"
        task_type: "classification"
    """

    name = "timm_classification"
    version = "1.0.0"
    category = "ml_backend"
    task_type = "classification"

    def install(self) -> None:
        """插件安装: 验证 timm 可用"""
        try:
            import timm  # noqa: F401, PLC0415
            logger.info(f"TimmClassificationPlugin v{self.version}: install (timm OK)")
        except ImportError:
            logger.warning(
                "TimmClassificationPlugin: timm not installed, "
                "build_model/predict/predict will fail at runtime"
            )

    def uninstall(self) -> None:
        """插件卸载: 无状态, 无需清理"""
        logger.info("TimmClassificationPlugin: uninstall (no-op)")

    # ============== MLBackendInterface 适配方法 ==============
    def build_model(self, base_model: str, num_classes: int, **kwargs: Any) -> Any:
        """构建 timm 模型实例

        Args:
            base_model: timm 模型名 (e.g. "efficientnet_b0", "resnet50")
            num_classes: 分类数
            **kwargs: 透传给 build_timm_model (pretrained, drop_rate, etc.)

        Returns:
            timm 模型实例 (torch.nn.Module)
        """
        from app.tasks.ml.classification import build_timm_model  # noqa: PLC0415
        logger.debug(f"TimmClassificationPlugin: build_model {base_model} num_classes={num_classes}")
        return build_timm_model(
            model_name=base_model,
            num_classes=num_classes,
            **kwargs,
        )

    def train(
        self,
        model: Any,
        train_data: Any,
        val_data: Any,
        **kwargs: Any,
    ) -> Any:
        """训练模型

        **重要**: 实际训练仍走 Celery 异步任务 (app.tasks.workers.classification.run_training),
        本方法主要用于: 单元测试/快速验证场景, 不推荐在生产环境直接调用.

        Args:
            model: timm 模型实例
            train_data: 训练数据 (DataLoader)
            val_data: 验证数据 (DataLoader)
            **kwargs: epochs / lr / 训练回调等

        Returns:
            训练结果 (含 best_acc / confusion_matrix / history)
        """
        from app.tasks.ml.classification import train_classifier  # noqa: PLC0415
        epochs = kwargs.pop("epochs", 10)
        lr = kwargs.pop("lr", 1e-3)
        device = kwargs.pop("device", "cpu")
        logger.info(
            f"TimmClassificationPlugin: train epochs={epochs} lr={lr} device={device}"
        )
        return train_classifier(
            model=model,
            train_loader=train_data,
            val_loader=val_data,
            epochs=epochs,
            lr=lr,
            device=device,
            **kwargs,
        )

    def predict(
        self,
        model: Any,
        image: Any,
        **kwargs: Any,
    ) -> Any:
        """单图推理

        Args:
            model: timm 模型实例 (或 checkpoint 路径)
            image: PIL.Image / numpy.ndarray / torch.Tensor
            **kwargs: device / top_k / threshold

        Returns:
            预测结果 (List[Dict[class_name, confidence]])
        """
        from app.tasks.ml.classification import predict_classifier  # noqa: PLC0415
        top_k: Optional[int] = kwargs.pop("top_k", None)
        device = kwargs.pop("device", "cpu")
        class_names = kwargs.pop("class_names", None)
        logger.debug(f"TimmClassificationPlugin: predict top_k={top_k} device={device}")
        return predict_classifier(
            model=model,
            image=image,
            top_k=top_k,
            device=device,
            class_names=class_names,
        )


# ============== 自动注册 ==============
PluginRegistry.register(TimmClassificationPlugin(), make_default=True)


__all__ = ["TimmClassificationPlugin"]
