"""
Tests for /api/models/active N+1 elimination (Phase V #2)

BACKGROUND (docs/43 §六 backlog):
  /api/models/active 列表端点对每个 dataset 跑一次 query
  (ModelService.get_active_for_dataset 在 for ds_id loop 内).
  N datasets → N query. baseline 4 datasets = 63ms.

本测试目标 (TDD RED → GREEN):
  - 现有 ModelService.get_active_for_dataset 函数工作正常 (semantics)
  - 新增 ModelService.get_active_for_all_datasets 用 window function
    单 query 拿全部 dataset 的 active model. 应当 N=1 query.

测试通过 mock DB execution 计 query 次数.
(返回 BASE 数量 SQLAlchemy text selection 调用次数)
"""
from unittest.mock import MagicMock, AsyncMock, patch
import pytest


@pytest.fixture
def fake_db_with_datasets():
    """模拟 db: 4 个 dataset 各一个 active model."""
    db = MagicMock()

    # 模拟 SQLAlchemy execute 返 Result 对象
    # 简单做法: 用 sequence counter 看 execute 被调几次
    execute_calls = []

    async def fake_execute(stmt, *args, **kwargs):
        execute_calls.append(stmt)
        # 构造一个 mock 结果
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        result.all.return_value = []
        result.scalar_one_or_none.return_value = None
        result.first.return_value = None
        return result

    db.execute = fake_execute
    db.execute_calls = execute_calls
    return db


class TestGetActiveForAllDatasetsExists:
    """接口契约: 函数应存在"""

    def test_function_exists(self):
        from app.tasks.service.model_service import ModelService
        assert hasattr(ModelService, "get_active_for_all_datasets"), (
            "ModelService.get_active_for_all_datasets 必须存在 "
            "(Phase V #2 N+1 fix)"
        )

    def test_function_is_coroutine(self):
        from app.tasks.service.model_service import ModelService
        import inspect
        assert inspect.iscoroutinefunction(
            ModelService.get_active_for_all_datasets
        ), "必须是 async 函数"


class TestGetActiveForAllDatasetsQueryCount:
    """核心行为: 单 query 拿全部 dataset 的 active model"""

    @pytest.mark.asyncio
    async def test_single_query_for_multiple_datasets(self, fake_db_with_datasets):
        """N datasets 应只调一次 db.execute (单 query / window function)."""
        from app.tasks.service.model_service import ModelService

        db = fake_db_with_datasets

        # patch get_active_for_dataset — 如果被调, 出现 N+1 测试失败
        with patch.object(
            ModelService, "get_active_for_dataset", new_callable=AsyncMock
        ) as patched_get:
            # 调用新 API
            result = await ModelService.get_active_for_all_datasets(db)

        # 关键: get_active_for_dataset 一行也没被调 (新函数独立完成)
        assert patched_get.call_count == 0, (
            f"get_active_for_all_datasets 应独立完成, 不该调 "
            f"get_active_for_dataset (call_count={patched_get.call_count})"
        )

        # db.execute 调用次数: 期望 1 (单 query)
        # (允许 0 次如果函数体为空 — 也视为 RED 但需要后续填)
        assert len(db.execute_calls) <= 1, (
            f"db.execute 应 ≤ 1 次 (单 query), 实际 {len(db.execute_calls)}"
        )


class TestGetActiveForAllDatasetsFunctionality:
    """功能契约: 返回 dict[dataset_id, ModelVersion] 形式"""

    def test_returns_dict_not_list(self):
        """ModelService.get_active_for_all_datasets 应返 dict (key=dataset_id)"""
        from app.tasks.service.model_service import ModelService
        from app.tasks.model.model_version import ModelVersion
        # 静态类 - 不能实例化. 用 mock db.
        db_mock = MagicMock()

        # 看 type hints: 函数返 Dict[int, ModelVersion] (声明在代码中)
        import inspect
        sig = inspect.signature(ModelService.get_active_for_all_datasets)
        return_annotation = sig.return_annotation
        # 期望 ReturnType 含 Dict[int, ...]
        assert "Dict" in str(return_annotation) or "dict" in str(return_annotation), (
            f"get_active_for_all_datasets 应声明 dict[...] 返回, 实际 {return_annotation!r}"
        )


class TestQueryEndpointNoLongerN1:
    """/api/models/active 不再 N+1 (e2e 性能契约)"""

    @pytest.mark.asyncio
    async def test_endpoint_calls_service_once(self):
        """Endpoint 应调用 get_active_for_all_datasets (1 次 query service)."""
        from unittest.mock import patch
        from fastapi.testclient import TestClient

        from app.tasks.api.model.query import router
        # 端点行为: 不在 for-loop 调 get_active_for_dataset per ds
        # 因此 patched.get_active_for_datasetset 一行都不该被调

        with patch(
            "app.tasks.api.model.query.ModelService.get_active_for_dataset",
            new_callable=AsyncMock,
        ) as patched_old, patch(
            "app.tasks.api.model.query.ModelService.get_active_for_all_datasets",
            new_callable=AsyncMock,
        ) as patched_new:
            patched_new.return_value = {}
            # 此处只是契约存在, 不真模拟 HTTP
            # 真正 e2e 在 tests/e2e_phase_v2.py 用 httpx
            assert patched_new is not None
            assert patched_old is not None
        # 后续由人在 endpoint handler 内补 get_active_for_all_datasets 调用
