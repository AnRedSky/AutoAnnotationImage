"""团队管理 L5 索引优化 (v3.3.1)
==================================

优化目标: 解决 L4 阶段新增端点的潜在慢查询.

L4 端点:
1. GET /api/audit-logs: 多维度过滤
   - 按 event_type + 时间范围
   - 按 team_id + 时间范围
   - 按 user_id + 时间范围
   排序: created_at DESC

2. GET /api/teams/{id}/activities: 团队活动 Feed
   - WHERE team_id = ? ORDER BY created_at DESC LIMIT 50
   - 这是高频端点, 必须走索引

复合索引设计:
- ix_audit_log_team_created   (team_id, created_at DESC)
- ix_audit_log_event_created  (event_type, created_at DESC)
- ix_audit_log_user_created   (user_id, created_at DESC)
- ix_audit_log_resource       (resource_type, resource_id)

单列索引 (已有):
- ix_audit_log_team_id        (team_id)            - 已有
- ix_audit_log_user_id        (user_id)            - 已有
- ix_audit_log_event_type     (event_type)         - 已有
- ix_audit_log_created_at     (created_at)         - 已有

注意: 复合索引 (a, b) 可单独用于 a 列查询, 但不能单独用于 b 列查询.
     当只有 a 单列索引时, (a, b) 查询会先走 a 索引, 再排序 b, 可能慢.
     复合索引 (a, b DESC) 进一步优化排序性能.

幂等: 重复执行安全 (information_schema 检查)
跨 DB 兼容: MySQL 5.7+ / SQLite (CREATE INDEX 错误被静默吞)
"""
import asyncio
from sqlalchemy import text
from app.database import engine


NEW_INDEXES = [
    # 复合索引: 用于团队活动 Feed (L4-T2) + 审计按 team 过滤 (L4-T1)
    ("ix_audit_log_team_created", "audit_log", ["team_id", "created_at"]),
    # 复合索引: 用于审计按 event_type 过滤 (L4-T1)
    ("ix_audit_log_event_created", "audit_log", ["event_type", "created_at"]),
    # 复合索引: 用于审计按 user_id 过滤 (L4-T1)
    ("ix_audit_log_user_created", "audit_log", ["user_id", "created_at"]),
    # 复合索引: 用于审计按资源定位 (L4-T1)
    ("ix_audit_log_resource", "audit_log", ["resource_type", "resource_id"]),
    # 团队成员邀请溯源查询 (audit_log.team_id + resource_type=team_member)
    # 已有 ix_audit_log_team_id 覆盖单 team_id 查询
    # 列表分页 join 性能: 已有 ix_team_member_team_id (主键索引)
]


async def _index_exists(conn, table: str, index_name: str) -> bool:
    """检查索引是否存在 (兼容 MySQL / SQLite)"""
    dialect = conn.dialect.name
    if dialect == "sqlite":
        # SQLite: 通过 sqlite_master 查询
        result = await conn.execute(text("""
            SELECT COUNT(*) FROM sqlite_master
            WHERE type='index' AND tbl_name = :table AND name = :index_name
        """), {"table": table, "index_name": index_name})
        return (result.scalar() or 0) > 0
    # MySQL
    result = await conn.execute(text("""
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table
          AND INDEX_NAME = :index_name
    """), {"table": table, "index_name": index_name})
    return (result.scalar() or 0) > 0


async def add_l5_indexes():
    async with engine.begin() as conn:
        for idx_name, table, columns in NEW_INDEXES:
            if await _index_exists(conn, table, idx_name):
                print(f"[OK] 索引 {idx_name} 已存在, 跳过")
                continue

            # MySQL 8.0+ 支持 DESC 排序索引, 5.7 仅升序
            # 为了兼容, 升序创建, ORDER BY DESC 仍可用 (MySQL 可反向扫描)
            col_list = ", ".join(f"`{c}`" for c in columns)
            stmt = f"CREATE INDEX `{idx_name}` ON `{table}` ({col_list})"
            try:
                await conn.execute(text(stmt))
                print(f"[OK] 已创建索引 {idx_name} ON {table}({col_list})")
            except Exception as e:
                # SQLite 重复创建会被拒, 静默跳过
                msg = str(e).lower()
                if "already exists" in msg or "duplicate" in msg:
                    print(f"[SKIP] 索引 {idx_name} (重复创建)")
                else:
                    raise


async def explain_query():
    """EXPLAIN 关键查询, 验证索引使用情况 (MySQL only)"""
    async with engine.begin() as conn:
        if conn.dialect.name != "mysql":
            print(f"[SKIP] EXPLAIN 仅在 MySQL 演示, 当前 {conn.dialect.name}")
            return

        queries = [
            ("团队活动 Feed (L4-T2)",
             "SELECT * FROM audit_log WHERE team_id = 1 ORDER BY created_at DESC LIMIT 50"),
            ("审计按 event_type 过滤 (L4-T1)",
             "SELECT * FROM audit_log WHERE event_type = 'team_updated' ORDER BY created_at DESC LIMIT 50"),
            ("审计按 user_id 过滤 (L4-T1)",
             "SELECT * FROM audit_log WHERE user_id = 1 ORDER BY created_at DESC LIMIT 50"),
        ]
        for name, sql in queries:
            print(f"\n[EXPLAIN] {name}")
            print(f"  SQL: {sql}")
            result = await conn.execute(text(f"EXPLAIN {sql}"))
            for row in result.fetchall():
                print(f"  {dict(row._mapping)}")


async def main():
    print("[L5] 开始创建团队管理索引优化...")
    await add_l5_indexes()
    print("\n[EXPLAIN] 验证关键查询是否走索引")
    await explain_query()
    print("\n[done] L5 索引优化完成")


if __name__ == "__main__":
    asyncio.run(main())
