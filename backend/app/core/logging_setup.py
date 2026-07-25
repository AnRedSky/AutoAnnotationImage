"""
Logging Setup (Core Layer)
==========================

集中配置应用日志格式, 避免各模块重复配置.

**v3.0.0 Stage 3 新增**:
- 统一日志格式 (timestamp, level, logger, message)
- 控制台 + 文件双输出 (按 settings.APP_DEBUG 自动切换)
- 与 uvicorn 日志格式一致 (避免格式混乱)

**使用方式**:
```python
from app.core.logging_setup import setup_logging
setup_logging()  # 应用启动时调用一次
```

**依赖方向**: core/logging_setup 依赖 core/config, 不依赖任何业务层.
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from app.core.config import settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-30s | %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
_FILE_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-7s | %(name)-30s | "
    "%(pathname)s:%(lineno)d | %(message)s"
)


def setup_logging(
    log_dir: Optional[Path] = None,
    console_level: Optional[str] = None,
    file_level: Optional[str] = None,
) -> None:
    """初始化全局日志配置

    Args:
        log_dir: 日志目录. 默认 `<project_root>/logs/`
        console_level: 控制台日志级别. 默认 INFO (生产) / DEBUG (开发)
        file_level: 文件日志级别. 默认 INFO
    """
    # 1) 解析参数
    if log_dir is None:
        from app.core.config import _PROJECT_ROOT
        log_dir = _PROJECT_ROOT / "logs"
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    is_dev = settings.APP_ENV == "development"
    console_level = console_level or ("DEBUG" if is_dev else "INFO")
    file_level = file_level or "INFO"

    # 2) 根 logger 配置
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # 清空已有 handler (避免重复添加, 每次启动都干净)
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    # 3) 控制台 handler (简短格式)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, console_level))
    console_formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # 4) 文件 handler (详细格式, 包含堆栈行号)
    log_file = log_dir / "app.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(getattr(logging, file_level))
    file_formatter = logging.Formatter(_FILE_LOG_FORMAT, datefmt=_LOG_DATEFMT)
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # 5) 第三方库日志降噪 (避免无用信息淹没业务日志)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("celery").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    root_logger.info(
        f"Logging configured: env={settings.APP_ENV} "
        f"console={console_level} file={file_level} path={log_file}"
    )


def get_logger(name: str) -> logging.Logger:
    """获取命名 logger (统一入口, 便于未来注入 trace_id)

    Args:
        name: 通常是 `__name__`, e.g. "app.tasks.service.training_service"

    Returns:
        配置好的 logger 实例
    """
    return logging.getLogger(name)


__all__ = ["setup_logging", "get_logger"]
