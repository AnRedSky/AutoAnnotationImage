"""
Shared Device Info Collector — app/tasks/ml/device_info.py
=========================================================

采集训练资源信息 (device_type / device_name / CUDA / GPU 显存 / CPU 核数 / RAM /
torch 版本 / python 版本).

**v3.0.0 抽离**: 从 `ml/classification.py` 移出, 供 classification / detection /
segmentation 三个训练 worker 共享调用, 避免重复实现 + 跨任务类型设备记录一致性.

返回 dict 全部字段都是 JSON 安全的 (str / int / float / None), 序列化时不会爆炸.
用于写入 `TrainingJob.device_info` (DB JSON 字段) + SSE meta 推给前端展示.
"""
import os
import platform
from typing import Any, Dict

import torch


def collect_device_info() -> Dict[str, Any]:
    """
    采集训练资源信息 (device_type / device_name / CUDA / GPU 显存 / CPU 核数 / RAM /
    torch 版本 / python 版本).

    - 优先 CUDA (Nvidia GPU)
    - 其次 MPS (Apple Silicon Mac Metal)
    - 兜底 CPU

    返回 dict 全部字段都是 JSON 安全的 (str / int / float / None), 序列化时不会爆炸.
    用于写入 TrainingJob.device_info (DB JSON 字段) + SSE meta 推给前端展示.
    """
    info: Dict[str, Any] = {
        "device_type": "cpu",
        "device_name": platform.processor() or "CPU",
        "device_index": 0,
        "cuda_version": None,
        "cudnn_version": None,
        "torch_version": torch.__version__,
        "python_version": platform.python_version(),
        "os_platform": platform.platform(),
        "gpu_count": 0,
        "gpu_memory_mb": None,
        "gpu_memory_total_mb": None,
        "cpu_count": os.cpu_count(),
        "ram_gb": None,
    }
    # RAM (可选: psutil 没装就跳过)
    try:
        import psutil
        vmem = psutil.virtual_memory()
        info["ram_gb"] = round(vmem.total / (1024 ** 3), 2)
        info["ram_available_gb"] = round(vmem.available / (1024 ** 3), 2)
    except Exception:
        pass
    # CUDA 优先
    if torch.cuda.is_available():
        try:
            info["device_type"] = "cuda"
            info["gpu_count"] = torch.cuda.device_count()
            info["device_name"] = torch.cuda.get_device_name(0)
            info["device_index"] = 0
            info["cuda_version"] = torch.version.cuda
            try:
                info["cudnn_version"] = torch.backends.cudnn.version()
            except Exception:
                pass
            props = torch.cuda.get_device_properties(0)
            info["gpu_memory_total_mb"] = round(props.total_memory / (1024 ** 2))
            # 当前空闲显存 (训练前的 snapshot, 真实峰值在 GPU 监控回调里更新)
            try:
                free, total = torch.cuda.mem_get_info(0)
                info["gpu_memory_mb"] = round(total / (1024 ** 2))
            except Exception:
                pass
        except Exception as e:
            info["device_name"] = f"CUDA available but device detection failed: {e!r}"
    # MPS (Apple Silicon)
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        try:
            info["device_type"] = "mps"
            info["device_name"] = f"Apple Silicon MPS ({platform.processor()})"
        except Exception:
            info["device_name"] = "Apple Silicon MPS"
    return info


def select_device(device_info: Dict[str, Any]) -> torch.device:
    """
    根据采集结果构造 torch.device.

    核心要求: 优先 GPU (CUDA), 其次 MPS, 兜底 CPU.
    - 若 torch.cuda.is_available() 报 True 但 load 失败, 退回 CPU 并通过
      device_info["fallback_reason"] 记录原因 (供前端显示「CUDA 不可用, 已退回 CPU」)
    """
    preferred = device_info.get("device_type", "cpu")
    try:
        if preferred == "cuda":
            return torch.device("cuda")
        if preferred == "mps":
            return torch.device("mps")
    except Exception as e:
        device_info["fallback_reason"] = f"{preferred} device construction failed: {e!r}"
    return torch.device("cpu")


def extract_job_device_fields(device_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    从 collect_device_info() 返回的完整 dict 中提取需要写入 TrainingJob 表的 4 个字段:
    - device_type (str)
    - device_name (str)
    - device_info (完整 dict, 存 JSON 列)
    - gpu_peak_memory_mb (int | None, CUDA 时取 gpu_memory_total_mb 作为初始值;
      真实峰值由训练过程中的监控回调更新)

    供 worker 把这 4 字段塞进 sticky_meta, mark_success 统一持久化.
    """
    return {
        "device_type": str(device_info.get("device_type", "cpu")),
        "device_name": str(device_info.get("device_name", "CPU")),
        "device_info": device_info,
        "gpu_peak_memory_mb": (
            int(device_info["gpu_memory_total_mb"])
            if device_info.get("device_type") == "cuda"
               and device_info.get("gpu_memory_total_mb") is not None
            else None
        ),
    }


__all__ = ["collect_device_info", "select_device", "extract_job_device_fields"]
