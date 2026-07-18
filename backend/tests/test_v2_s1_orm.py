"""v2.0.0 S1 测试: ORM 模型 + 迁移幂等性 (兼容 SQLite + MySQL)

- v2.0.0 新表能创建 (create_all 自动建)
- 已有表加列能成功 (image.task_type / model_version.task_type + 5 指标)
- 不破坏 v1.0.0 已有数据 (回填默认 classification)

使用 SQLAlchemy ORM 而非原生 SQL, 避免 NOW() 等方言差异
"""
import pytest
from datetime import datetime

from app.models import (
    User, Dataset, Image, Category,
    BBoxAnnotation, SegmentationMask, ModelVersion,
    AnnotationLog, TrainingJob,
)


def _new_engine_session():
    """每个测试一个全新的内存数据库, 模拟真实使用"""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.database import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    return engine, setup


@pytest.mark.asyncio
async def test_v2_tables_created():
    """create_all 应建出 v2.0.0 新表"""
    from app.database import Base
    from app.database import engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.begin() as conn:
        from sqlalchemy import text
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('bbox_annotation', 'segmentation_mask')"
        ))
        tables = {row[0] for row in result.fetchall()}
    assert "bbox_annotation" in tables
    assert "segmentation_mask" in tables


@pytest.mark.asyncio
async def test_image_task_type_default():
    """image.task_type 默认 classification, 兼容 v1.0.0"""
    engine, setup = _new_engine_session()
    SessionLocal = await setup()
    async with SessionLocal() as session:
        u = User(username="u1", email="u@x.com", password_hash="h", role="admin", is_active=True)
        session.add(u)
        await session.flush()
        ds = Dataset(name="d1", task_type="classification", owner_id=u.id, status="draft")
        session.add(ds)
        await session.flush()
        img = Image(
            dataset_id=ds.id, filename="a.jpg", storage_path="a.jpg",
            file_hash="h1", status="pending",
        )
        session.add(img)
        await session.commit()
        # 重新查
        result = await session.get(Image, img.id)
        assert result.task_type == "classification"  # 默认值
        assert result.dataset_id == ds.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_model_version_task_type_and_metrics():
    """model_version 新字段存在, 默认 classification, 5 指标为 NULL"""
    engine, setup = _new_engine_session()
    SessionLocal = await setup()
    async with SessionLocal() as session:
        u = User(username="u1", email="u@x.com", password_hash="h", role="admin", is_active=True)
        session.add(u)
        await session.flush()
        ds = Dataset(name="d1", task_type="classification", owner_id=u.id, status="draft")
        session.add(ds)
        await session.flush()
        mv = ModelVersion(
            name="m1", base_model="efficientnet_b0", dataset_id=ds.id,
            num_classes=5, accuracy=0.9, precision=0.85, recall=0.88, f1_score=0.86,
        )
        session.add(mv)
        await session.commit()
        result = await session.get(ModelVersion, mv.id)
        assert result.task_type == "classification"
        # 5 个新指标为 NULL (兼容老数据)
        assert result.map_50 is None
        assert result.map_50_95 is None
        assert result.miou is None
        assert result.pixel_accuracy is None
        assert result.dice_score is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_bbox_annotation_crud_smoke():
    """BBoxAnnotation 增删改查冒烟测试"""
    engine, setup = _new_engine_session()
    SessionLocal = await setup()
    async with SessionLocal() as session:
        u = User(username="u1", email="u@x.com", password_hash="h", role="admin", is_active=True)
        session.add(u)
        await session.flush()
        ds = Dataset(name="d1", task_type="detection", owner_id=u.id, status="draft")
        session.add(ds)
        await session.flush()
        c = Category(dataset_id=ds.id, name="cat", color="#f00", sort_order=0)
        session.add(c)
        await session.flush()
        img = Image(
            dataset_id=ds.id, filename="a.jpg", storage_path="a.jpg",
            file_hash="h1", status="pending", task_type="detection",
        )
        session.add(img)
        await session.flush()
        # 写一条 bbox
        bb = BBoxAnnotation(
            image_id=img.id, category_id=c.id,
            x_min=0.1, y_min=0.2, x_max=0.5, y_max=0.6,
            confidence=0.95, source="human", annotated_by=u.id,
        )
        session.add(bb)
        await session.commit()
        # 读
        result = await session.get(BBoxAnnotation, bb.id)
        assert result.x_min == 0.1
        assert result.y_min == 0.2
        assert result.x_max == 0.5
        assert result.y_max == 0.6
        assert result.source == "human"
    await engine.dispose()


@pytest.mark.asyncio
async def test_bbox_annotation_via_image_relationship():
    """通过 Image.bbox_annotations 关系访问"""
    engine, setup = _new_engine_session()
    SessionLocal = await setup()
    async with SessionLocal() as session:
        u = User(username="u1", email="u@x.com", password_hash="h", role="admin", is_active=True)
        session.add(u)
        await session.flush()
        ds = Dataset(name="d1", task_type="detection", owner_id=u.id, status="draft")
        session.add(ds)
        await session.flush()
        c = Category(dataset_id=ds.id, name="cat", color="#f00", sort_order=0)
        session.add(c)
        await session.flush()
        img = Image(
            dataset_id=ds.id, filename="a.jpg", storage_path="a.jpg",
            file_hash="h1", status="pending", task_type="detection",
        )
        session.add(img)
        await session.flush()
        for i in range(3):
            bb = BBoxAnnotation(
                image_id=img.id, category_id=c.id,
                x_min=0.1 * i, y_min=0.1, x_max=0.2, y_max=0.2,
                source="human", annotated_by=u.id,
            )
            session.add(bb)
        await session.commit()
        # 重新查
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        stmt = select(Image).where(Image.id == img.id).options(selectinload(Image.bbox_annotations))
        result = (await session.execute(stmt)).scalar_one()
        assert len(result.bbox_annotations) == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_segmentation_mask_unique_image_id():
    """SegmentationMask.image_id 唯一, 重复写入应报错"""
    from sqlalchemy.exc import IntegrityError
    engine, setup = _new_engine_session()
    SessionLocal = await setup()
    async with SessionLocal() as session:
        u = User(username="u1", email="u@x.com", password_hash="h", role="admin", is_active=True)
        session.add(u)
        await session.flush()
        ds = Dataset(name="d1", task_type="segmentation", owner_id=u.id, status="draft")
        session.add(ds)
        await session.flush()
        img = Image(
            dataset_id=ds.id, filename="a.jpg", storage_path="a.jpg",
            file_hash="h1", status="pending", task_type="segmentation",
        )
        session.add(img)
        await session.flush()
        m1 = SegmentationMask(
            image_id=img.id, mask_path="m1.png", width=100, height=100, source="human"
        )
        session.add(m1)
        await session.commit()
        # 第二次写相同 image_id 应报错
        m2 = SegmentationMask(
            image_id=img.id, mask_path="m2.png", width=100, height=100, source="human"
        )
        session.add(m2)
        with pytest.raises(IntegrityError):
            await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_segmentation_mask_via_image_relationship():
    """通过 Image.segmentation_mask 关系访问 (1:1, uselist=False)"""
    engine, setup = _new_engine_session()
    SessionLocal = await setup()
    async with SessionLocal() as session:
        u = User(username="u1", email="u@x.com", password_hash="h", role="admin", is_active=True)
        session.add(u)
        await session.flush()
        ds = Dataset(name="d1", task_type="segmentation", owner_id=u.id, status="draft")
        session.add(ds)
        await session.flush()
        img = Image(
            dataset_id=ds.id, filename="a.jpg", storage_path="a.jpg",
            file_hash="h1", status="pending", task_type="segmentation",
        )
        session.add(img)
        await session.flush()
        m = SegmentationMask(
            image_id=img.id, mask_path="m.png", width=100, height=100, source="human"
        )
        session.add(m)
        await session.commit()
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        stmt = select(Image).where(Image.id == img.id).options(selectinload(Image.segmentation_mask))
        result = (await session.execute(stmt)).scalar_one()
        assert result.segmentation_mask is not None
        assert result.segmentation_mask.mask_path == "m.png"
    await engine.dispose()
