"""
Datetime Utilities (Utils Layer)
===============================

时间格式化、解析、计算的统一工具. 集中处理:
- ISO 8601 格式化 (前端 / DB 一致)
- UTC ↔ 本地时间转换
- 时间差计算 (训练时长, 任务耗时)
- 友好时间显示 ("2 minutes ago")

**v3.0.0 Stage 3 新增**.

**依赖**: 纯标准库 (datetime, time), 无第三方依赖.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Union


def utc_now() -> datetime:
    """获取当前 UTC 时间 (aware datetime)"""
    return datetime.now(tz=timezone.utc)


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    """datetime → ISO 8601 字符串 (含时区)

    Args:
        dt: datetime 对象 (允许 None)

    Returns:
        ISO 字符串, e.g. "2026-07-25T09:45:00+00:00"
        None → None
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # 朴素 datetime 视为 UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def from_iso(s: Optional[str]) -> Optional[datetime]:
    """ISO 8601 字符串 → datetime (含时区)

    Args:
        s: ISO 字符串, e.g. "2026-07-25T09:45:00+00:00"
           也接受 naive 字符串 (无时区), 视为 UTC

    Returns:
        aware datetime, 失败 / None → None
    """
    if not s:
        return None
    try:
        # 兼容 'Z' 后缀 (Python 3.11+ 内置 fromisoformat 已支持)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def humanize_duration(seconds: Union[int, float, None]) -> str:
    """秒数 → 人类可读时长 (e.g. "2h 35m 12s")

    Args:
        seconds: 秒数 (允许负数 / None)

    Returns:
        人类可读字符串, e.g. "2h 35m 12s" / "45s" / "1d 3h"
    """
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 0:
        return f"-{humanize_duration(-seconds)}"
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, min_ = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {min_}m"
    days, h = divmod(hours, 24)
    return f"{days}d {h}h"


def time_ago(dt: Optional[datetime], now: Optional[datetime] = None) -> str:
    """返回 "X 秒前" / "X 分钟前" / "X 小时前" / "X 天前" 风格字符串

    Args:
        dt: 目标时间
        now: 当前时间 (默认 utc_now()), 主要用于测试

    Returns:
        e.g. "刚刚" / "5 分钟前" / "3 小时前" / "2 天前" / "2026-07-25"
    """
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if now is None:
        now = utc_now()
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "未来"  # 时钟漂移保护
    if seconds < 60:
        return "刚刚"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    days = hours // 24
    if days < 7:
        return f"{days} 天前"
    # 7 天以上: 直接显示日期
    return dt.strftime("%Y-%m-%d")


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """确保 datetime 是 UTC (naive 视为 UTC, aware 转 UTC)"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


__all__ = [
    "utc_now",
    "to_iso",
    "from_iso",
    "humanize_duration",
    "time_ago",
    "ensure_utc",
]
