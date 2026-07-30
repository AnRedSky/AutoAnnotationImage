"""
Phase V #2 v2: is_active 优先级回归测试

背景:
  Phase V #2 用 ROW_NUMBER 单 query 替代 N+1, 但 ROW_NUMBER 的 ORDER BY
  没加 ``is_active DESC`` → 语义和旧的 ``get_active_for_dataset`` 不一致.

  旧语义 (get_active_for_dataset):
    1. 找 is_active=true 的, 取第一个
    2. 找不到, fallback 到 versions[0]

  新语义 (list_active_per_dataset):
    ROW_NUMBER ORDER BY 第一列必须是 is_active 降序, 让 is_active=true 永远 rn=1.
"""
import pytest


class TestIsActivePriorityInRowNumber:
    """ROW_NUMBER 的 ORDER BY 必须含 is_active 降序."""

    def test_compiled_sql_contains_is_active_in_order_by(self):
        """编译 list_active_per_dataset 的 SQL, 检查 ORDER BY 含 is_active.

        方法: 用 mock db 截获 SQLAlchemy 编译出的 SQL 字符串,
        检查其中 ORDER BY 子句含 is_active.
        """
        from sqlalchemy.ext.asyncio import AsyncSession
        from unittest.mock import MagicMock, AsyncMock

        captured_sql = []

        class FakeResult:
            def all(self): return []
            def scalars(self):
                class S:
                    def all(self): return []
                return S()

        async def fake_execute(stmt, *args, **kwargs):
            # 编译 SQL 到字符串
            captured_sql.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
            return FakeResult()

        db = MagicMock()
        db.execute = fake_execute

        from app.tasks.repository.model_version_queries import list_active_per_dataset
        import asyncio

        asyncio.get_event_loop().run_until_complete(list_active_per_dataset(db))

        assert len(captured_sql) >= 1, "db.execute 应至少被调一次"
        sql = captured_sql[0].lower()
        # ROW_NUMBER OVER ... ORDER BY ... 必须含 is_active
        assert "is_active" in sql, (
            f"编译的 SQL 应在 ROW_NUMBER ORDER BY 中含 is_active 列. "
            f"实际 SQL: {sql[:500]}"
        )
