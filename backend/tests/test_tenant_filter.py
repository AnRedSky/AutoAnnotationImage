"""
Tenant filter 真测试 (MT-4 验证)

验证 do_orm_execute event listener 在 TenantContext.set(tenant_id) 后
自动给 SELECT 加 WHERE tenant_id = :tid.
"""
import asyncio
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

# 全局 import (fixture 和 test 方法共用)
from app.common.base_model import Base
from app.admin.model.tenant import Tenant
from app.tasks.model.dataset import Dataset


@pytest.fixture
async def tenant_db():
    """sqlite 内存 DB + tenant + dataset 表 + 测试数据."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 插入测试数据
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        t1 = Tenant(id=1, name="t1", slug="t1")
        t2 = Tenant(id=2, name="t2", slug="t2")
        session.add_all([t1, t2])
        await session.flush()
        d1 = Dataset(id=1, name="ds_t1", owner_id=1, tenant_id=1)
        d2 = Dataset(id=2, name="ds_t2", owner_id=1, tenant_id=2)
        session.add_all([d1, d2])
        await session.commit()

    yield SessionLocal
    await engine.dispose()


class TestTenantFilterBasics:
    """do_orm_execute 自动过滤基本行为."""

    @pytest.mark.asyncio
    async def test_tenant_1_only_sees_own_datasets(self, tenant_db):
        """TenantContext.set(1) → 只返 tenant_id=1 的 dataset."""
        from app.middleware.tenant import TenantContext

        async with tenant_db() as session:
            TenantContext.set(1)
            try:
                result = await session.execute(select(Dataset))
                datasets = result.scalars().all()
                # 应只返 tenant_id=1 的
                tenant_ids = {d.tenant_id for d in datasets}
                assert tenant_ids == {1}, f"应只返 tenant_id=1, 实际 {tenant_ids}"
            finally:
                TenantContext.clear()

    @pytest.mark.asyncio
    async def test_tenant_2_only_sees_own_datasets(self, tenant_db):
        """TenantContext.set(2) → 只返 tenant_id=2 的 dataset."""
        from app.middleware.tenant import TenantContext

        async with tenant_db() as session:
            TenantContext.set(2)
            try:
                result = await session.execute(select(Dataset))
                datasets = result.scalars().all()
                tenant_ids = {d.tenant_id for d in datasets}
                assert tenant_ids == {2}, f"应只返 tenant_id=2, 实际 {tenant_ids}"
            finally:
                TenantContext.clear()

    @pytest.mark.asyncio
    async def test_super_admin_sees_all(self, tenant_db):
        """TenantContext.set(None) → super_admin 跳过过滤, 返全部."""
        from app.middleware.tenant import TenantContext

        async with tenant_db() as session:
            TenantContext.set(None)
            try:
                result = await session.execute(select(Dataset))
                datasets = result.scalars().all()
                assert len(datasets) == 2, f"super_admin 应看到全部 2 个, 实际 {len(datasets)}"
            finally:
                TenantContext.clear()

    @pytest.mark.asyncio
    async def test_no_context_sees_all(self, tenant_db):
        """TenantContext 未 set (default None) → 不过滤."""
        from app.middleware.tenant import TenantContext

        # 确保是 None
        TenantContext.clear()
        async with tenant_db() as session:
            result = await session.execute(select(Dataset))
            datasets = result.scalars().all()
            assert len(datasets) == 2, f"无 tenant 上下文应看到全部, 实际 {len(datasets)}"
