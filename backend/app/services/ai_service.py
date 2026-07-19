"""
AI Service: Model Loading & Inference
=====================================
封装 timm 模型的加载、推理、批量处理
"""
import asyncio
import json
import re
from pathlib import Path
from typing import List, Dict, Optional, TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor

# v2.0.0 S2: timm/torch 改为延迟导入, 避免非 AI 链路测试 (如 bbox CRUD) 被强制拉
# 整个 torch (~2GB 运行时) 才能 import 该模块。实际加载/推理时再 import。
if TYPE_CHECKING:  # 仅类型注解用, 运行时无开销
    import timm
    import torch
    import torch.nn.functional as F

from PIL import Image


# ImageNet 1k 类别 (离线精简版, 演示用)
IMAGENET_DEMO_LABELS_PATH = Path(__file__).parent.parent / "ml" / "imagenet_demo_labels.json"


def _norm_label(s: str) -> str:
    """归一化: lowercase + 替换 _ 为空格 + 压缩空白 + 去首尾空白
    用于比较模型输出 (如 tabby_cat) 与项目类目 (如 Tabby Cat / tabby cat)
    """
    if not s:
        return ""
    s = str(s).lower().replace("_", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def filter_predictions_to_categories(
    predictions: List[Dict],
    category_names: List[str],
) -> List[Optional[Dict]]:
    """
    把 base model (timm ImageNet) 的 top-K 预测过滤到只含项目预设类目

    解决的问题:
      Base model 输出可能是 "class_579" / "tabby_cat" / "Egyptian_cat" 等
      与项目类目 ("室内空间" / "办公桌") 完全无关的标签, 不应作为 AI 预标注结果展示

    行为:
      - 输入: model 输出的预测列表 (top1/top1_conf/top5) + 项目类目名列表
      - 输出: 同长度列表, 每项要么是过滤+重命名后的新预测, 要么是 None (无匹配)
      - 匹配规则: 归一化后 (lowercase + _->空格 + 去首尾空格) 完全相等
      - 命中的标签改写为项目的类目名 (而不是模型原始输出), 保持 UI 展示一致
      - 项目无类目时, 全部返回 None (强制用户先加类目)

    典型场景:
      项目类目 = ["cat", "dog"]
      模型输出 = {"top1":"tabby_cat","top1_conf":0.7,"top5":[{"label":"tabby_cat","confidence":0.7},...]}
      过滤后 -> None (无交集)

      项目类目 = ["Tabby Cat", "Dog"]
      模型输出 = {"top1":"tabby_cat","top1_conf":0.7,"top5":[{"label":"tabby_cat","confidence":0.7},...]}
      过滤后 -> {"top1":"Tabby Cat","top1_conf":0.7,"top5":[{"label":"Tabby Cat","confidence":0.7}]}
    """
    # 建查找表: 归一化 -> 项目原名 (保留用户原始命名)
    norm_to_name: Dict[str, str] = {}
    for name in category_names or []:
        n = _norm_label(name)
        if n and n not in norm_to_name:
            norm_to_name[n] = name

    if not norm_to_name:
        # 项目无类目, 一律 no_match (调用方应先校验)
        return [None] * len(predictions or [])

    out: List[Optional[Dict]] = []
    for pred in predictions or []:
        if not pred or not pred.get("top5"):
            out.append(None)
            continue
        matched: List[Dict] = []
        for item in pred["top5"]:
            label = item.get("label", "")
            n = _norm_label(label)
            if n in norm_to_name:
                matched.append({
                    "label": norm_to_name[n],  # 用项目类目名覆盖
                    "confidence": float(item.get("confidence", 0.0)),
                })
        if not matched:
            out.append(None)
            continue
        out.append({
            "top1": matched[0]["label"],
            "top1_conf": matched[0]["confidence"],
            "top5": matched,
        })
    return out


class AIService:
    """深度学习推理服务 (单例)"""

    def __init__(self):
        # 设备选择：尊重 INFERENCE_DEVICE 配置
        # v2.0.0 S2: 延迟 import torch + 容错, 避免无 AI 调用路径的测试环境
        # (未装 timm/torch) 强制拉整个 torch (~2GB 运行时) 才能 import 本模块
        from app.config import settings
        try:
            import torch  # lazy
            if settings.INFERENCE_DEVICE == "cuda" and torch.cuda.is_available():
                self.device = torch.device("cuda")
            elif settings.INFERENCE_DEVICE == "cuda":
                # 用户要求 cuda 但不可用，回退 CPU 并打印警告
                print(f"[WARN] INFERENCE_DEVICE=cuda 但未检测到 CUDA，回退到 CPU")
                self.device = torch.device("cpu")
            else:
                self.device = torch.device("cpu")
        except ImportError:
            # 无 torch 环境下: 保留 None 占位, 实际调用 predict/load 时再报错
            self.device = None
        self.current_model = None
        self.current_model_name: Optional[str] = None
        self.current_model_path: Optional[str] = None
        # 自定义标签映射 (fine-tune 模式使用): idx -> 项目类目中文名
        # 设为 None 时退回 ImageNet demo labels / f"class_{idx}"
        self._custom_label_map: Optional[Dict[int, str]] = None
        # timm pretrained 模型的 1000 类 ImageNet 英文名 (lazy 填充)
        self._imagenet_label_lookup: Dict[int, str] = {}
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._demo_labels = self._load_demo_labels()

    def _load_demo_labels(self) -> Dict[int, str]:
        """加载离线演示用类别"""
        if IMAGENET_DEMO_LABELS_PATH.exists():
            with open(IMAGENET_DEMO_LABELS_PATH, encoding="utf-8") as f:
                names = json.load(f)
            return {i: n for i, n in enumerate(names)}
        return {}

    def set_label_map(self, label_map: Optional[Dict[int, str]]):
        """设置 / 清除自定义标签映射
        - 传 dict: 进入 fine-tune 模式, topk 索引用此 dict 翻译成项目类目
        - 传 None: 退回 ImageNet demo labels
        """
        self._custom_label_map = label_map

    def _build_transform(self, model):
        import timm.data
        cfg = timm.data.resolve_model_data_config(model)
        return timm.data.create_transform(**cfg, is_training=False)

    async def load_pretrained(self, model_name: str = "efficientnet_b0"):
        """异步加载 timm 预训练模型（带超时，避免无网络时长时间阻塞）"""
        import os
        import timm  # lazy: 只在显式加载预训练模型时引入
        # 缩短 HF Hub 的连接 / 读取超时（默认 10s/无限制 -> 3s/15s）
        os.environ.setdefault("HF_HUB_CONNECT_TIMEOUT", "3")
        os.environ.setdefault("HF_HUB_READ_TIMEOUT", "15")
        loop = asyncio.get_event_loop()
        try:
            self.current_model = await asyncio.wait_for(
                loop.run_in_executor(
                    self._executor,
                    lambda: timm.create_model(model_name, pretrained=True).to(self.device).eval()
                ),
                timeout=45.0,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Model load timeout (45s) for '{model_name}'. "
                "Check network or pre-download the model."
            )
        self.current_model_name = model_name
        self.current_model_path = None
        return self.current_model

    async def load_local(self, model_name: str, model_path: str, num_classes: int):
        """异步加载本地训练好的模型"""
        import timm  # lazy
        import torch  # lazy
        loop = asyncio.get_event_loop()
        model = await loop.run_in_executor(
            self._executor,
            lambda: timm.create_model(model_name, pretrained=False, num_classes=num_classes)
        )
        state_dict = await loop.run_in_executor(
            self._executor,
            lambda: torch.load(model_path, map_location=self.device)
        )
        model.load_state_dict(state_dict)
        model.to(self.device).eval()
        self.current_model = model
        self.current_model_name = model_name
        self.current_model_path = model_path
        return model

    def _predict_sync(self, image_path: str, top_k: int) -> Dict:
        """同步推理 (在线程池执行)"""
        import torch
        import torch.nn.functional as F  # lazy
        img = Image.open(image_path).convert("RGB")
        transform = self._build_transform(self.current_model)
        input_tensor = transform(img).unsqueeze(0).to(self.device)
        with torch.no_grad():
            logits = self.current_model(input_tensor)
            probs = F.softmax(logits, dim=1)
            # 关键: 模型可能只有 N 类 (fine-tune 项目类目), 不能硬 topk(5)
            # 例: 训练 2 类的模型, topk(5) 会 RuntimeError: selected index k out of range
            num_classes = probs.shape[-1]
            actual_k = max(1, min(top_k, num_classes))
            top_probs, top_indices = probs.topk(actual_k)

        # 构建 ImageNet 标签查找表 (lazy, 一次)
        # timm pretrained model 的 default_cfg["classes"] 是 list[str], 1000 个
        imagenet_classes = self.current_model.default_cfg.get("classes") or []
        if isinstance(imagenet_classes, list) and not self._imagenet_label_lookup:
            self._imagenet_label_lookup = {i: n for i, n in enumerate(imagenet_classes)}

        results = []
        for prob, idx in zip(top_probs[0], top_indices[0]):
            idx_int = idx.item()
            # 优先级: 自定义 label map (fine-tune) > imagenet 1000 (timm pretrained)
            #          > demo labels (ImageNet 精简) > class_X 兜底
            if self._custom_label_map is not None and idx_int in self._custom_label_map:
                label = self._custom_label_map[idx_int]
            else:
                label = (
                    self._imagenet_label_lookup.get(idx_int)
                    or self._demo_labels.get(idx_int)
                    or f"class_{idx_int}"
                )
            results.append({
                "label": label,
                "confidence": round(prob.item(), 4),
            })
        return {
            "top1": results[0]["label"],
            "top1_conf": results[0]["confidence"],
            "top5": results,
        }

    async def predict(self, image_path: str, top_k: int = 5) -> Dict:
        """单张图片推理"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._predict_sync, image_path, top_k
        )

    async def batch_predict(self, image_paths: List[str], top_k: int = 5) -> List[Dict]:
        """批量推理 (并发)"""
        tasks = [self.predict(p, top_k) for p in image_paths]
        return await asyncio.gather(*tasks)


# 全局单例
ai_service = AIService()
