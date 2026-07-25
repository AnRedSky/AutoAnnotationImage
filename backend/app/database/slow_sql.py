"""
SQL Slow Query Monitor (Database Layer)
=======================================

SQLAlchemy event listener, 监控慢查询并记录到 Redis (供 /api/metrics 聚合).

**Stage 5.4 新增**.

**工作原理**:
- 监听 SQLAlchemy `before_cursor_execute` / `after_cursor_execute` 事件
- 每次查询开始时记录开始时间, 结束时计算耗时
- 超过阈值 (默认 200ms) 时:
  1. WARNING 日志 (SQL + 耗时 + 堆栈)
  2. 写入 Redis ZSET `sql:slow_log` (按时间排序, 保留最近 1000 条)
  3. 计数 `sql:slow_count` (供 metrics 读取)

**降级**: Redis 不可用时仅写日志, 不抛异常.

v3.0.0 Stage 5.4
"""
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.core.config import settings

logger = logging.getLogger(__name__)

# SQLAlchemy event 在 import 时一次性绑定, 不重复绑定
_BOUND = False

# 内部缓冲: conn_id -> start_time (SQLAlchemy 不传 query 上下文, 需自己 key)
_QUERY_TIMES: Dict[int, float] = {}
_SLOW_SQL_MAX = 1000  # Redis ZSET 保留最近 1000 条


def _now_ms() -> float:
    return time.perf_counter() * 1000


def setup_slow_sql_monitor(engine: Engine) -> None:
    """绑定 SQLAlchemy 事件监听

    Args:
        engine: SQLAlchemy Engine 实例 (sync/async 通用)
    """
    global _BOUND
    if _BOUND:
        logger.debug("setup_slow_sql_monitor: already bound, skip")
        return

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        conn_id = id(conn)
        _QUERY_TIMES[conn_id] = _now_ms()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        conn_id = id(conn)
        start = _QUERY_TIMES.pop(conn_id, None)
        if start is None:
            return
        duration_ms = _now_ms() - start
        threshold = settings.SQL_SLOW_THRESHOLD_MS
        if duration_ms < threshold:
            return

        # 慢 SQL 触发: 日志 + Redis 记录
        _log_slow_sql(statement, parameters, duration_ms)
        _record_slow_sql(statement, duration_ms)

    _BOUND = True
    logger.info(
        f"setup_slow_sql_monitor: bound to engine, threshold={settings.SQL_SLOW_THRESHOLD_MS}ms"
    )


def _log_slow_sql(statement: str, parameters: Any, duration_ms: float) -> None:
    """记录慢 SQL 到日志 (WARNING)"""
    # 截断长 SQL 避免日志爆炸
    sql_short = statement[:500] + ("..." if len(statement) > 500 else "")
    params_short = str(parameters)[:200]
    logger.warning(
        f"[SLOW SQL] {duration_ms:.1f}ms > {settings.SQL_SLOW_THRESHOLD_MS}ms | "
        f"SQL={sql_short} | params={params_short}"
    )


def _record_slow_sql(statement: str, duration_ms: float) -> None:
    """记录慢 SQL 到 Redis (ZSET, 供 metrics 读取)"""
    try:
        from app.core.redis_client import redis_client  # noqa: PLC0415
        ts = time.time()
        # entry: timestamp + duration + sql 截断
        entry = f"{ts:.3f}|{duration_ms:.1f}|{statement[:200]}"
        # ZADD 进 sql:slow_log (按 ts 排序)
        redis_client.zadd("sql:slow_log", {entry: ts})
        # 仅保留最近 1000 条
        redis_client.zremrangebyrank("sql:slow_log", 0, -(_SLOW_SQL_MAX + 1))
        # INCR 慢 SQL 计数
        redis_client.incr("sql:slow_count")
    except Exception as e:  # noqa: BLE001
        # Redis 不可用仅 DEBUG, 不影响主流程
        logger.debug(f"_record_slow_sql failed: {e!r}")


def get_slow_sql_log(limit: int = 50) -> List[Dict[str, Any]]:
    """读取最近 N 条慢 SQL (从 Redis ZSET, 供 /api/metrics)

    Args:
        limit: 最多返回条数 (默认 50)

    Returns:
        List of {"ts": float, "duration_ms": float, "sql": str}
    """
    try:
        from app.core.redis_client import redis_client  # noqa: PLC0415
        # ZRANGE 按 score (ts) 升序, 取最近 limit 条
        entries = redis_client.zrange("sql:slow_log", -limit, -1)
        out: List[Dict[str, Any]] = []
        for e in entries:
            parts = e.split("|", 2)
            if len(parts) < 3:
                continue
            try:
                out.append({
                    "ts": float(parts[0]),
                    "duration_ms": float(parts[1]),
                    "sql": parts[2],
                })
            except ValueError:
                continue
        return out
    except Exception:  # noqa: BLE001
        return []


def get_slow_sql_count() -> int:
    """获取累计慢 SQL 计数"""
    try:
        from app.core.redis_client import redis_client  # noqa: PLC0415
        return int(redis_client.get("sql:slow_count") or 0)
    except Exception:  # noqa: BLE001
        return 0


def reset_slow_sql_stats() -> None:
    """重置慢 SQL 统计 (主要用于测试)"""
    try:
        from app.core.redis_client import redis_client  # noqa: PLC0415
        redis_client.delete("sql:slow_log", "sql:slow_count")
    except Exception:  # noqa: BLE001
        pass


def get_stats() -> Dict[str, Any]:
    """获取慢 SQL 监控统计 (供 /api/metrics 汇总)"""
    return {
        "threshold_ms": settings.SQL_SLOW_THRESHOLD_MS,
        "total_count": get_slow_sql_count(),
        "recent": get_slow_sql_log(limit=10),
    }


__all__ = [
    "setup_slow_sql_monitor",
    "get_slow_sql_log",
    "get_slow_sql_count",
    "reset_slow_sql_stats",
    "get_stats",
]
