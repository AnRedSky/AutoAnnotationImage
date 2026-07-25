"""
Utils Package — 纯函数工具 (横切复用)
===================================

包含:
- 异步桥 (async_helpers) — Celery worker 内跑 async 代码
- 时间 (datetime_utils) — Stage 3 新增
- 文件 (file_utils) — Stage 3 新增
- 分页 (pagination) — Stage 3 计划
- SSE 流 (sse) — Stage 3 计划
- 字符串 (string_utils) — Stage 3 计划
- 哈希 (hash_utils) — Stage 3 计划
- 图像 (image_utils) — Stage 3 计划
- 校验器 (validators) — Stage 3 计划

依赖方向: utils 必须是纯函数, 不依赖任何业务层, 不依赖 common/core/database/middleware.
"""
# Stage 3 新增: 命名导出, 便于 `from app.utils import humanize_duration` 风格导入
from app.utils.datetime_utils import (  # noqa: E402, F401
    utc_now,
    to_iso,
    from_iso,
    humanize_duration,
    time_ago,
    ensure_utc,
)
from app.utils.file_utils import (  # noqa: E402, F401
    format_size,
    safe_filename,
    guess_mime_type,
    file_md5,
    file_sha256,
    ensure_dir,
)

__all__ = [
    # datetime
    "utc_now", "to_iso", "from_iso", "humanize_duration", "time_ago", "ensure_utc",
    # file
    "format_size", "safe_filename", "guess_mime_type", "file_md5", "file_sha256", "ensure_dir",
]
