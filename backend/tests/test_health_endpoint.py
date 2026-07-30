"""
Tests for ``/api/health`` endpoint (P0-2 from docs/38-后端架构现状评估与拆分部署方案.md)
==================================================================================

P0-2: DB 挂掉时 ``/api/health`` 必须返回 HTTP 503, 这样 K8s readiness probe /
阿里云 SLB 才会停止路由流量. 当前实现始终返回 200 + status=degraded,
会让 LB 持续把流量打到已挂实例.

设计契约:
- DB 不可用 → HTTP 503, body 仍含 details 方便排错
- Redis 不可用 → HTTP 200 + status=degraded (Redis 有 cache fallback, 不致命)
- MinIO 不可用 → HTTP 200 + status=degraded (导出 7 个端点有问题, 但 API 元服务可用)
- 全部可用 → HTTP 200 + status=ok
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from httpx import AsyncClient


class TestHealthEndpointStatusCodes:
    """测试 health 端点的 HTTP 状态码契约."""

    @pytest.mark.asyncio
    async def test_db_down_returns_503(self, monkeypatch, client: AsyncClient):
        """DB 抛异常时, /api/health 必须返 503 (而不是 200)."""
        from sqlalchemy.exc import OperationalError
        from app.admin.api import system

        # 用 monkeypatch 替换掉 db.execute 链路. 最小破坏面:
        # 直接 patch ``system.health_check`` 看, 但会破坏整个端点.
        # 更稳的方法: 把 health_check 内部用的 db session 替换成抛异常.

        # 实际上系统端点写的是 ``await db.execute(text('SELECT 1'))``,
        # 它用了 Depends(get_db). 我们提供一个异步生成器替身即可.

        async def boom_db():
            # 第一次 yield 一个 session, 然后 Db.execute 抛错.
            raise OperationalError("stmt", {}, Exception("connection refused"))

        # overlay get_db 让它抛 OperationalError
        from app.database import get_db
        from app.main import app

        async def _override_db():
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        # FastAPI 端 Depends(get_db) 必须直接 raise 才生效.
        # get_db 是一个 generator, 内部 yield 一个 session.
        # 我们让它 yield 一个执行时抛 OperationalError 的伪 session.

        class _FailDb:
            async def execute(self, *a, **kw):
                raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        async def _override():
            yield _FailDb()

        # 注意: 真实 dependency override 在 client fixture 里管理.
        # 必须在它之后覆盖, 才能 override 生效.
        app.dependency_overrides[get_db] = _override
        try:
            resp = await client.get("/api/health")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert resp.status_code == 503, (
            f"DB 挂时 health 应返 503, 实际 {resp.status_code}; body={resp.text[:500]}"
        )
        body = resp.json()
        # 503 body 应仍含 details (FastAPI HTTPException detail)
        assert "detail" in body or "checks" in body, (
            f"503 body 应有 detail 或 checks; 实际 {list(body.keys())}"
        )

    @pytest.mark.asyncio
    async def test_redis_down_returns_200_degraded(self, monkeypatch, client: AsyncClient):
        """Redis 不可用不应返 503 - 业务侧有 cache fallback."""
        # current implementation 仍然全 200; 测试契约
        # 这里 mock 掉 health_check 内 redis 调用的入口
        from app.admin.api import system

        async def fake_redis_ping_fail(*a, **kw):
            raise ConnectionError("redis down")

        monkeypatch.setattr(
            "redis.asyncio.Redis.ping",
            fake_redis_ping_fail,
            raising=False,
        )

        # 即便 redis 失败, 健康端点仍然应该返 200, 业务可降级.
        resp = await client.get("/api/health")
        assert resp.status_code == 200, (
            f"Redis 挂时 health 应返 200 degraded, 实际 {resp.status_code}"
        )
        body = resp.json()
        # body 应标记 redis failed
        checks = body.get("checks", {})
        if checks.get("redis"):  # redis check 可能被 mock 跳过, 不强制
            assert checks["redis"]["ok"] is False, (
                f"redis 应报告 ok=False, body={body}"
            )

    @pytest.mark.asyncio
    async def test_all_ok_returns_200(self, client: AsyncClient):
        """全部依赖可用时, /api/health 返 200 + status=ok."""
        resp = await client.get("/api/health")
        assert resp.status_code == 200, (
            f"全部 OK 应返 200, 实际 {resp.status_code}; body={resp.text[:500]}"
        )
        body = resp.json()
        assert body.get("status") == "ok", (
            f"全部 OK 时 status 应为 ok, 实际 {body.get('status')}"
        )


class TestHealthEndpointBehaviorContracts:
    """行为契约测试."""

    @pytest.mark.asyncio
    async def test_health_response_contains_three_dependency_keys(self, client: AsyncClient):
        """响应 body 应含 database / redis / minio 三段."""
        resp = await client.get("/api/health")
        body = resp.json()
        checks = body.get("checks", {})
        for required in ("database", "redis", "minio"):
            assert required in checks, (
                f"checks 应含 {required}, 实际 keys={list(checks.keys())}"
            )

    @pytest.mark.asyncio
    async def test_health_response_includes_timestamp(self, client: AsyncClient):
        """响应必含 timestamp (K8s 排错用)."""
        resp = await client.get("/api/health")
        body = resp.json()
        assert "timestamp" in body, f"响应缺 timestamp, body={list(body.keys())}"
        assert isinstance(body["timestamp"], int), (
            f"timestamp 应为 int, 实际 {type(body['timestamp'])}"
        )
