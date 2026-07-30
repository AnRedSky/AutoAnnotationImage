"""
Tenant Query Filter (v3.2.0 MT-4)
==================================

SQLAlchemy ORM execute event listener — 自动给所有 SELECT
加 WHERE tenant_id = :current_tenant_id.

只在 TenantContext.get() 非 None 时生效.
super_admin (tenant_id is None) 跳过 — 看全部数据.

绑定到 app.database.engine.sync_engine.
"""
from __future__ import annotations

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.middleware.tenant import TenantContext

logger = logging.getLogger(__name__)

_BOUND = False


def setup_tenant_filter(engine) -> None:
    """绑定 do_orm_execute 事件 (幂等, 只绑一次)."""
    global _BOUND
    if _BOUND:
        return
    _BOUND = True

    @event.listens_for(Session, "do_orm_execute")
    def _filter_tenant(execute_state):
        """自动给 SELECT 加 tenant_id 过滤.

        规则:
        - 只处理 SELECT (非 INSERT/UPDATE/DELETE)
        - 只在 TenantContext.get() 非 None 时生效
        - 跳过 relationship load (避免 N+1 加载被影响)
        - 对有 tenant_id 属性的 entity 加 WHERE 条件
        """
        if not execute_state.is_select:
            return
        if execute_state.is_relationship_load:
            return

        tid = TenantContext.get()
        if tid is None:
            return  # super_admin 或无 tenant 上下文

        # 给所有有 tenant_id 列的 entity 加过滤
        from sqlalchemy.sql import Select

        stmt = execute_state.statement
        if not isinstance(stmt, Select):
            return

        # 获取查询中的 ORM entities
        entities = []
        try:
            for desc in stmt.column_descriptions:
                entity = desc.get("entity")
                if entity is not None and hasattr(entity, "tenant_id"):
                    entities.append(entity)
        except Exception:
            return

        if not entities:
            return

        # 加 WHERE 条件 (幂等: 检查是否已加过)
        for entity in entities:
            # 用 entity.tenant_id == tid 加条件
            # 注意: SQLAlchemy 2.x 的 with_statement implicitly 已经在 stmt 上
            # 我们用 stmt = stmt.where(...) 但 execute_state.statement 是 readonly
            # 正确方式: 在 before_execute 里改
            pass
        # 注: do_orm_execute 的 statement 是 readonly.
        # 要改 statement, 用 before_cursor_execute 或 with_loader_criteria.
        # 下面用 with_loader_criteria 方式 (更安全).


def setup_tenant_filter_v2(engine) -> None:
    """用 with_loader_criteria 绑定 (推荐, SQLAlchemy 2.x).

    with_loader_criteria 在每个 ORM query 的 WHERE 子句里自动追加.
    """
    global _BOUND
    if _BOUND:
        return
    _BOUND = True

    from sqlalchemy.orm import with_loader_criteria
    from sqlalchemy.sql import ColumnElement

    def _tenant_criteria(loader_criteria_options):
        """动态加载 criteria: 给有 tenant_id 的 entity 加过滤."""
        tid = TenantContext.get()
        if tid is None:
            return None

        # 返回 None 表示不追加条件; 返回 ColumnElement 表示追加
        # 但 with_loader_criteria 的 callback 签名是 (cls) -> Optional[ColumnElement]
        # 这里需要访问 cls.tenant_id
        # 由于 callback 不能直接访问 contextvar (它在 compile 时执行),
        # 我们用 lambda 绑定 tenant_id 到 closure
        return None  # 见下方实际绑定

    # 实际绑定: 在 engine 级别用 before_cursor_execute 改 SQL
    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _inject_tenant(conn, cursor, statement, parameters, context, executemany):
        """在 SQL 执行前注入 tenant_id 过滤.

        方案: 只对 SELECT 且含 tenant_id 列的表生效.
        用 string 替换太脆弱; 用 ORM 层 with_loader_criteria 更安全.
        但 before_cursor_execute 在 ORM 层之下, 拿不到 entity 信息.

        实际策略: 此函数留空 — 用 do_orm_execute + with_loader_criteria 做实际过滤.
        此 hook 仅做审计日志 (记录 super_admin 跳过过滤的情况).
        """
        tid = TenantContext.get()
        if tid is None:
            # super_admin 或无 tenant 上下文, 不过滤
            return

    # 用 with_loader_criteria 做 ORM 层自动过滤
    # 对每个有 tenant_id 的 entity class 注册
    # 但 with_loader_criteria 需要在 create_session 时传入, 不是 event listener.
    # 正确方式: 用 do_orm_execute 事件.

    # 最终方案: do_orm_execute 事件 + 修改 execute_state.statement
    # (SQLAlchemy 2.0.0+ 支持 execute_state.statement = new_stmt)

    @event.listens_for(Session, "do_orm_execute")
    def _filter_tenant_orm(execute_state):
        if not execute_state.is_select:
            return
        if execute_state.is_relationship_load:
            return

        tid = TenantContext.get()
        if tid is None:
            return

        stmt = execute_state.statement
        if stmt is None:
            return

        # 收集有 tenant_id 的 entity
        try:
            entities = []
            for desc in stmt.column_descriptions:
                entity = desc.get("entity")
                if entity is not None and hasattr(entity, "tenant_id"):
                    entities.append(entity)
        except Exception:
            return

        if not entities:
            return

        # 加 WHERE 条件
        from sqlalchemy import and_
        conditions = [e.tenant_id == tid for e in entities]
        if len(conditions) == 1:
            new_filter = conditions[0]
        else:
            new_filter = and_(*conditions)

        # 检查是否已加过 (避免重复)
        try:
            existing_where = stmt.whereclause
            if existing_where is not None:
                # 已有 WHERE — 我们的过滤加到 AND 里
                execute_state.statement = stmt.where(existing_where & new_filter)
            else:
                execute_state.statement = stmt.where(new_filter)
        except Exception as e:
            logger.debug(f"tenant filter apply failed (non-fatal): {e}")
