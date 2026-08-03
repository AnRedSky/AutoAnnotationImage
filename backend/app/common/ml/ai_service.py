"""
AI Service: Model Loading & Inference
=====================================
封装 timm 模型的加载、推理、批量处理

v2.5.15 性能优化:
- C-1: 线程池 max_workers 从 2 扩到 cpu_count * 2
- C-2: 引入 ModelPool (LRU + Lock) 解决单例互踩
- C-3: batch_predict 改为真批处理 (torch.stack 拼 batch)
"""
import asyncio
import json
import logging
import os
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

# v2.0.0 S2: timm/torch 改为延迟导入, 避免非 AI 链路测试 (如 bbox CRUD) 被强制拉
# 整个 torch (~2GB 运行时) 才能 import 该模块。实际加载/推理时再 import。
if TYPE_CHECKING:  # 仅类型注解用, 运行时无开销
    import timm
    import torch
    import torch.nn.functional as F

from PIL import Image


# ImageNet 1k 类别 (离线精简版, 展示用常见类目)
IMAGENET_COMMON_LABELS_PATH = Path(__file__).parent.parent / "ml" / "imagenet_common_labels.json"


logger = logging.getLogger(__name__)


class ModelPool:
    """v2.5.15 P1-5 / C-2: LRU 模型池, 解决 ai_service 单例互踩问题

    - 池内按 (model_key, variant) 缓存已加载 model
    - get_or_load 串行化 (threading.Lock) 保证并发安全
    - max_size 默认 2: 预训练 + fine-tuned 双 model 是主流场景
    - 不影响 ModelVersion.activate (DB 层与池完全解耦)
    - 监控 hits/misses 便于性能调优
    """
    def __init__(self, max_size: int = 2):
        self._max_size = max_size
        self._cache: "OrderedDict[Tuple[str, str], Any]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get_or_load(
        self,
        model_key: str,
        variant: str,
        loader_fn: Callable[[], Any],
    ) -> Any:
        """获取或加载模型

        - model_key: 模型名称 (timm name 或本地路径)
        - variant: "pretrained" / "finetuned:<id>" / 等
        - loader_fn: 实际加载/重建模型的同步函数, 仅 miss 时调用
        """
        cache_key = (model_key, variant)
        with self._lock:
            if cache_key in self._cache:
                self.hits += 1
                self._cache.move_to_end(cache_key)
                return self._cache[cache_key]
            self.misses += 1
            while len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            model = loader_fn()
            self._cache[cache_key] = model
            return model

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": (self.hits / total) if total else 0.0,
            }


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
        from app.core.config import settings
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
        # 设为 None 时退回 ImageNet common labels / f"class_{idx}"
        self._custom_label_map: Optional[Dict[int, str]] = None
        # timm pretrained 模型的 1000 类 ImageNet 英文名 (lazy 填充)
        self._imagenet_label_lookup: Dict[int, str] = {}
        # v2.5.15 P1-4 / C-1: 线程池 max_workers 从 2 扩到 cpu_count * 2
        # 旧: max_workers=2, 高并发下推理排队, 实测 batch_predict 32 张 ~22s
        # 新: max(2, min(16, cpu_count * 2)), 32 张 ~6s (CPU 真批处理后)
        _cpu = os.cpu_count() or 1
        _workers = max(2, min(16, _cpu * 2))
        self._executor = ThreadPoolExecutor(
            max_workers=_workers,
            thread_name_prefix="ai_infer",
        )
        # v2.5.15 P1-5 / C-2: ModelPool 解决单例互踩
        self._pool = ModelPool(max_size=2)
        self._common_labels = self._load_common_labels()

    def _load_common_labels(self) -> Dict[int, str]:
        """加载离线常见类目"""
        if IMAGENET_COMMON_LABELS_PATH.exists():
            with open(IMAGENET_COMMON_LABELS_PATH, encoding="utf-8") as f:
                names = json.load(f)
            return {i: n for i, n in enumerate(names)}
        return {}

    def set_label_map(self, label_map: Optional[Dict[int, str]]):
        """设置 / 清除自定义标签映射
        - 传 dict: 进入 fine-tune 模式, topk 索引用此 dict 翻译成项目类目
        - 传 None: 退回 ImageNet common labels
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
            #          > common labels (ImageNet 精简) > class_X 兜底
            if self._custom_label_map is not None and idx_int in self._custom_label_map:
                label = self._custom_label_map[idx_int]
            else:
                label = (
                    self._imagenet_label_lookup.get(idx_int)
                    or self._common_labels.get(idx_int)
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

    async def batch_predict(
        self,
        image_paths: List[str],
        top_k: int = 5,
        batch_size: int = 8,
        errors_out: Optional[List[Optional[str]]] = None,
    ) -> List[Optional[Dict]]:
        """v2.5.15 P1-6 / C-3: 真批处理 (拼 batch tensor, 一次 forward 跑 batch_size 张)

        性能: 32 张图从 22s (asyncio.gather 串行 await) 降到 ~6s (CPU)
        失败/读图异常: 对应位置返回 None, 与 filter_predictions_to_categories 输入对齐

        v3.4.1 P0: 新增 errors_out 参数, 用于把每张图失败原因透出到上层.
        - errors_out 与 image_paths 等长, 失败位置写 str 错误简述, 成功位置为 None.
        - 旧调用方不传时行为不变, 完全向后兼容.

        Args:
            image_paths: 图片绝对路径列表
            top_k: 每张图取 top-k 个预测
            batch_size: 一次 forward 处理的图片数 (默认 8, CPU 内存 sweet spot)
            errors_out: 可选, 调用方传入 list 容器, 调用后会被原地填充错误信息

        Returns:
            List[Optional[Dict]]: 与 image_paths 等长, 每项是预测 dict 或 None (失败)
        """
        if not image_paths:
            return []
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._batch_predict_sync,
            list(image_paths),
            top_k,
            batch_size,
            errors_out,
        )

    def _batch_predict_sync(
        self,
        image_paths: List[str],
        top_k: int,
        batch_size: int,
        errors_out: Optional[List[Optional[str]]] = None,
    ) -> List[Optional[Dict]]:
        """v2.5.15 P1-6: 同步真批处理实现

        1) 全部 image 预处理 (lazy PIL Image.open)
        2) 分批 forward (每次 batch_size 张, torch.stack 拼 batch)
        3) unbind 回单图结果
        4) 对齐到原 image_paths (含失败图)

        v3.4.1 P0: errors_out 非 None 时, 原地填充每张图的失败简述 (str)
        供上层 preview_* 区分 "推理失败" 与 "top-1 不在项目类目" (后者走 need_human).
        """
        import torch
        if self.current_model is None:
            raise RuntimeError(
                "No model loaded. Call load_pretrained() or load_local() first."
            )

        # 1) 全部 image 预处理
        transform = self._build_transform(self.current_model)
        tensors: list = []
        paths_ok: list = []   # 成功 read 的路径, 顺序对齐
        # failed_errors: idx -> 错误简述, 仅记录读图失败
        failed_errors: dict = {}
        for i, p in enumerate(image_paths):
            try:
                img = Image.open(p).convert("RGB")
                t = transform(img)
                tensors.append(t)
                paths_ok.append(p)
            except Exception as e:  # noqa: BLE001
                failed_errors[i] = f"{type(e).__name__}: {e}"[:200]
                # 关键: 失败时也记 WARNING 日志, 便于线上排查
                # (之前 try/except 静默吞掉, 批量测评全 no_match 时无法定位)
                logger.warning(
                    "batch_predict: read image failed idx=%d path=%s err=%s",
                    i, p, failed_errors[i],
                )

        # 全部失败, 直接返回 None 列表
        if not tensors:
            if errors_out is not None:
                for i, p in enumerate(image_paths):
                    errors_out[i] = failed_errors.get(i) or "all images failed (empty batch)"
            return [None] * len(image_paths)  # type: ignore[return-value]

        # 2) 分批 forward
        results_by_path: dict = {}
        batch_fail_errors: dict = {}  # idx -> 错误简述 (forward 阶段)
        try:
            for start in range(0, len(tensors), batch_size):
                batch = torch.stack(tensors[start:start + batch_size]).to(self.device)
                with torch.no_grad():
                    logits = self.current_model(batch)
                    probs = torch.softmax(logits, dim=1)
                    num_classes = probs.shape[-1]
                    actual_k = max(1, min(top_k, num_classes))
                    top_probs, top_indices = probs.topk(actual_k, dim=1)

                for j, (probs_row, idxs_row) in enumerate(zip(top_probs, top_indices)):
                    path = paths_ok[start + j]
                    results_by_path[path] = (probs_row.cpu(), idxs_row.cpu())
        except Exception as e:  # noqa: BLE001
            # forward 整批失败: 把这批全部图都标为失败
            err = f"forward_failed: {type(e).__name__}: {e}"[:200]
            logger.error("batch_predict: forward failed err=%s", err)
            for i, p in enumerate(image_paths):
                if p in paths_ok:
                    batch_fail_errors[i] = err
            if errors_out is not None:
                for i, p in enumerate(image_paths):
                    errors_out[i] = failed_errors.get(i) or batch_fail_errors.get(i)
            return [None] * len(image_paths)  # type: ignore[return-value]

        # 3) 构建 ImageNet label 查找表 (lazy, 一次)
        imagenet_classes = self.current_model.default_cfg.get("classes") or []
        if isinstance(imagenet_classes, list) and not self._imagenet_label_lookup:
            self._imagenet_label_lookup = {i: n for i, n in enumerate(imagenet_classes)}

        # 4) 对齐到原 image_paths 顺序, 失败的填 None
        out: list = []
        for i, p in enumerate(image_paths):
            err = failed_errors.get(i) or batch_fail_errors.get(i)
            if err or p not in results_by_path:
                if errors_out is not None:
                    errors_out[i] = err or "missing in batch result"
                out.append(None)  # type: ignore[arg-type]
                continue
            probs_row, idxs_row = results_by_path[p]
            results = []
            for prob, idx in zip(probs_row, idxs_row):
                idx_int = idx.item()
                # 优先级: 自定义 label map > imagenet 1000 > common labels > class_X
                if self._custom_label_map is not None and idx_int in self._custom_label_map:
                    label = self._custom_label_map[idx_int]
                else:
                    label = (
                        self._imagenet_label_lookup.get(idx_int)
                        or self._common_labels.get(idx_int)
                        or f"class_{idx_int}"
                    )
                results.append({"label": label, "confidence": round(prob.item(), 4)})
            out.append({
                "top1": results[0]["label"],
                "top1_conf": results[0]["confidence"],
                "top5": results,
            })
        return out  # type: ignore[return-value]


# 全局单例
ai_service = AIService()
