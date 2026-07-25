"""
Startup Profiler (Core Layer)
=============================

记录应用启动各阶段耗时, 输出启动报告.

**Stage 5.1 新增**.

**使用方式**:
```python
from app.core.startup_profiler import startup_profiler

# 在 lifespan 中
with startup_profiler.step("init_db"):
    await init_db()

with startup_profiler.step("plugin_install"):
    PluginRegistry.install_all()

# 启动后输出报告
startup_profiler.report()
```

**输出示例**:
```
=== Startup Report ===
Total: 1.234s
  - init_db: 0.456s (37%)
  - plugin_install: 0.123s (10%)
  - ultralytics_config: 0.234s (19%)
  - app_startup: 0.421s (34%)
```

v3.0.0 Stage 5.1
"""
import logging
import time
from contextlib import contextmanager
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class StartupProfiler:
    """启动耗时分析器 (单例)"""

    _steps: List[Dict[str, Any]] = []
    _start_ts: float = 0.0

    @classmethod
    def begin(cls) -> None:
        """标记启动开始 (main.py lifespan 入口)"""
        cls._steps.clear()
        cls._start_ts = time.perf_counter()
        logger.debug("StartupProfiler: begin")

    @classmethod
    @contextmanager
    def step(cls, name: str):
        """计时段 (with 上下文)

        Args:
            name: 阶段名 (e.g. "init_db", "plugin_install")
        """
        step_start = time.perf_counter()
        logger.debug(f"StartupProfiler: step '{name}' begin")
        try:
            yield
        finally:
            duration = time.perf_counter() - step_start
            cls._steps.append({"name": name, "duration": duration})
            logger.debug(f"StartupProfiler: step '{name}' done in {duration:.3f}s")

    @classmethod
    def report(cls) -> Dict[str, Any]:
        """输出启动报告 (启动完成后调用)

        Returns:
            {
                "total_ms": 1234.5,
                "steps": [
                    {"name": "init_db", "duration_ms": 456.0, "percent": 37.0},
                    ...
                ]
            }
        """
        if cls._start_ts == 0.0:
            return {"total_ms": 0.0, "steps": []}

        total = time.perf_counter() - cls._start_ts
        steps_out = []
        for s in cls._steps:
            dur_ms = s["duration"] * 1000
            pct = (s["duration"] / total * 100) if total > 0 else 0.0
            steps_out.append({
                "name": s["name"],
                "duration_ms": round(dur_ms, 2),
                "percent": round(pct, 1),
            })

        # 按耗时降序, 便于快速定位慢启动
        steps_out.sort(key=lambda x: x["duration_ms"], reverse=True)

        report = {
            "total_ms": round(total * 1000, 2),
            "steps": steps_out,
        }
        # 人类可读输出
        lines = [f"=== Startup Report === Total: {total:.3f}s"]
        for s in steps_out:
            lines.append(f"  - {s['name']}: {s['duration_ms']:.0f}ms ({s['percent']}%)")
        logger.info("\n".join(lines))
        return report

    @classmethod
    def reset(cls) -> None:
        """重置 (主要用于测试)"""
        cls._steps.clear()
        cls._start_ts = 0.0


# 全局单例
startup_profiler = StartupProfiler()


__all__ = ["startup_profiler", "StartupProfiler"]
