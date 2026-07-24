"""
Model Version API: List / Activate / Deactivate / Compare / Delete / BatchDelete
===========================================
模型版本管理: 列出所有版本、激活/取消激活某个版本、对比效果、删除版本

激活语义 (v2 改造):
- 支持同 dataset 下多激活并存
- 同 dataset 全部取消激活: 用户主动调用 batch-deactivate 或逐个 deactivate
- 「激活」是软状态, 用于标注工作台识别"可用模型集合"
- 删除激活模型: 允许, 删除即取消激活 (前端也放开限制)
"""
import os
import logging
from typing import Optional, List
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.database import get_db
from app.models.model_version import ModelVersion
from app.models.dataset import Dataset
from app.models.training_job import TrainingJob
from app.models.user import User
from app.core.deps import get_current_user
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


# ============== Schemas ==============
class BatchDeleteRequest(BaseModel):
    ids: List[int] = Field(..., min_length=1, max_length=200,
                           description="待删除的模型版本 id 列表, 数量 1-200")


class BatchActivateRequest(BaseModel):
    """批量激活 / 批量取消激活的通用 body"""
    ids: List[int] = Field(..., min_length=1, max_length=200,
                           description="模型版本 id 列表, 数量 1-200")
    active: bool = Field(..., description="True=激活, False=取消激活")


# ============== 行锁工具 ==============
async def _lock_dataset_models(db: AsyncSession, dataset_id: Optional[int]) -> list[ModelVersion]:
    """
    锁住指定 dataset 的所有 ModelVersion 行 (SELECT ... FOR UPDATE)
    dataset_id 为 None 时锁全表 (用于批量操作)
    """
    stmt = select(ModelVersion)
    if dataset_id is not None:
        stmt = stmt.where(ModelVersion.dataset_id == dataset_id)
    stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalars().all()


async def _delete_one_model(db: AsyncSession, m: ModelVersion) -> dict:
    """
    单个模型版本删除内部实现, 与 DELETE /{model_id} 行为一致.
    返回: {"id", "deleted_file": bool}
    调用方需自行管理事务/锁.
    """
    file_path = m.file_path
    deleted_file = False
    if file_path:
        try:
            abs_path = os.path.abspath(file_path)
            models_dir = str(settings.MODEL_DIR)
            if abs_path.startswith(models_dir + os.sep) and os.path.isfile(abs_path):
                still_ref = await db.execute(
                    select(ModelVersion.id).where(
                        ModelVersion.file_path == m.file_path,
                        ModelVersion.id != m.id,
                    )
                )
                refs = still_ref.scalars().all()
                if refs:
                    logger.info("Keep file %s: still referenced by %d other version(s) %s",
                                abs_path, len(refs), list(refs))
                else:
                    os.remove(abs_path)
                    deleted_file = True
                    logger.info("Deleted model file: %s", abs_path)
        except Exception as e:
            logger.warning("Failed to delete model file %s: %s", file_path, e)
    await db.delete(m)
    return {"id": m.id, "deleted_file": deleted_file}


@router.get("/")
@router.get("")  # 同时支持 /api/models 和 /api/models/ (前端曾用 /models 报 404)
async def list_models(
    dataset_id: Optional[int] = Query(default=None, description="按数据集 id 过滤"),
    active: Optional[bool] = Query(default=None, description="按激活状态过滤: true/false"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    列出所有模型版本
    - 可选过滤: dataset_id, active (按激活状态)
    - 同时返回关联 Dataset 的 name, 供前端表格「训练集」列展示
    """
    stmt = select(ModelVersion)
    if dataset_id is not None:
        stmt = stmt.where(ModelVersion.dataset_id == dataset_id)
    if active is not None:
        stmt = stmt.where(ModelVersion.is_active == active)  # noqa: E712
    stmt = stmt.order_by(ModelVersion.created_at.desc())
    result = await db.execute(stmt)
    models = result.scalars().all()

    # 预查 dataset_name (兼容没建 relationship 的情况)
    dataset_ids = sorted({m.dataset_id for m in models if m.dataset_id})
    ds_map: dict[int, str] = {}
    if dataset_ids:
        ds_rows = (await db.execute(
            select(Dataset.id, Dataset.name).where(Dataset.id.in_(dataset_ids))
        )).all()
        for row in ds_rows:
            ds_map[row.id] = row.name

    return {
        "items": [
            {
                "id": m.id,
                "name": m.name,
                "base_model": m.base_model,
                # v2.5.24: 前端按 task_type 过滤 finetune models
                # (同一 dataset 可能多 task_type 模型并存, 不加这个字段前端无法分辨)
                "task_type": m.task_type,
                "dataset_id": m.dataset_id,
                "dataset_name": ds_map.get(m.dataset_id) if m.dataset_id else None,
                # v2.5.16: 任务类型 (classification/detection/segmentation)
                # 此前未返回, 前端兜底 "|| 'classification'" 把所有模型都显示为"图片分类"
                "task_type": m.task_type,
                "num_classes": m.num_classes,
                "accuracy": float(m.accuracy or 0),
                "precision": float(m.precision or 0),
                "recall": float(m.recall or 0),
                "f1_score": float(m.f1_score or 0),
                # 检测专属指标 (m.task_type == "detection" 时非空)
                "map_50": float(m.map_50) if m.map_50 is not None else None,
                "map_50_95": float(m.map_50_95) if m.map_50_95 is not None else None,
                # 分割专属指标 (m.task_type == "segmentation" 时非空)
                "miou": float(m.miou) if m.miou is not None else None,
                "pixel_accuracy": float(m.pixel_accuracy) if m.pixel_accuracy is not None else None,
                "dice_score": float(m.dice_score) if m.dice_score is not None else None,
                "is_active": m.is_active,
                "created_at": m.created_at.isoformat(),
            }
            for m in models
        ]
    }


@router.get("/active")
async def list_active_models(
    dataset_id: Optional[int] = Query(default=None, description="数据集 ID; 缺省=全局所有激活模型"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    列出当前激活的模型版本 (供标注工作台 / 概览面板)
    - 支持同 dataset 下多激活并存, 返回 list 而非 single
    - dataset_id 指定: 查该 dataset 下 is_active 的全部
    - dataset_id 缺省: 全局所有 is_active

    返回: { items: [{id, name, base_model, dataset_id, dataset_name, accuracy, is_active, ...}] }
    """
    stmt = select(ModelVersion).where(ModelVersion.is_active == True)  # noqa: E712
    if dataset_id is not None:
        stmt = stmt.where(ModelVersion.dataset_id == dataset_id)
    stmt = stmt.order_by(ModelVersion.id.desc())
    rows = (await db.execute(stmt)).scalars().all()

    # 预查 dataset_name
    ds_ids = sorted({m.dataset_id for m in rows if m.dataset_id})
    ds_map: dict[int, str] = {}
    if ds_ids:
        ds_rows = (await db.execute(
            select(Dataset.id, Dataset.name).where(Dataset.id.in_(ds_ids))
        )).all()
        for r in ds_rows:
            ds_map[r.id] = r.name

    return {
        "items": [
            {
                "id": m.id,
                "name": m.name,
                "base_model": m.base_model,
                "dataset_id": m.dataset_id,
                "dataset_name": ds_map.get(m.dataset_id) if m.dataset_id else None,
                # v2.5.16: 任务类型透出 (与 list_models 对齐)
                "task_type": m.task_type,
                "num_classes": m.num_classes,
                "accuracy": float(m.accuracy or 0),
                "f1_score": float(m.f1_score or 0),
                "is_active": m.is_active,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ]
    }


@router.post("/{model_id}/activate")
async def activate_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    激活指定模型版本 (v2 语义)
    - 不再取消同 dataset 下其他激活 (允许多激活并存)
    - 若目标当前已是激活状态, 视为幂等 (返回 success=True)
    """
    target = await db.get(ModelVersion, model_id)
    if not target:
        raise HTTPException(404, "Model not found")

    try:
        # 仅锁目标行, 不动其他行
        locked = await _lock_dataset_models(db, target.dataset_id)
        _ = locked  # 加锁后立刻修改目标即可
        target.is_active = True
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, f"Failed to activate model: {e}")

    return {
        "success": True,
        "active_model_id": model_id,
        "dataset_id": target.dataset_id,
        "is_active": target.is_active,
    }


@router.post("/{model_id}/deactivate")
async def deactivate_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    取消激活指定模型版本
    - 不影响其他模型的状态
    - 幂等: 已经是未激活的也返回 success=True
    """
    target = await db.get(ModelVersion, model_id)
    if not target:
        raise HTTPException(404, "Model not found")

    try:
        locked = await _lock_dataset_models(db, target.dataset_id)
        _ = locked
        target.is_active = False
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, f"Failed to deactivate model: {e}")

    return {
        "success": True,
        "deactivated_model_id": model_id,
        "dataset_id": target.dataset_id,
        "is_active": target.is_active,
    }


@router.post("/batch-activate")
async def batch_set_active(
    body: BatchActivateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量激活 / 批量取消激活 (active=true|false)
    - 同一事务, 全部成功或全部回滚
    - 不区分 dataset: 用户可一次性"全部激活"或"全部取消激活"
    - 返回: { success, ids, active }
    """
    # 去重保持顺序
    seen: set[int] = set()
    uniq_ids: list[int] = []
    for i in body.ids:
        if i not in seen:
            seen.add(i)
            uniq_ids.append(i)

    rows = (await db.execute(
        select(ModelVersion).where(ModelVersion.id.in_(uniq_ids))
    )).scalars().all()
    found_map = {m.id: m for m in rows}

    missing = [i for i in uniq_ids if i not in found_map]
    if missing:
        raise HTTPException(404, f"模型版本不存在: {missing}")

    try:
        # 锁全部目标行
        _ = (await db.execute(
            select(ModelVersion).where(ModelVersion.id.in_(uniq_ids)).with_for_update()
        )).scalars().all()
        for m in rows:
            m.is_active = body.active
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error("batch-set-active failed: %s", e)
        raise HTTPException(500, f"批量操作失败: {e}")

    return {
        "success": True,
        "ids": uniq_ids,
        "active": body.active,
    }


@router.get("/{model_id}/detail")
async def get_model_detail(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取模型详情 (含训练曲线、混淆矩阵、激活状态等)

    返回字段与列表接口 (/api/models) 保持一致, 避免前端弹窗显示与列表
    不一致 (例如激活状态: 列表显示「已激活」但弹窗显示「未激活」).
    """
    m = await db.get(ModelVersion, model_id)
    if not m:
        raise HTTPException(404, "Model not found")

    # 同步返回 dataset_name (前端弹窗可能用)
    ds_name = None
    if m.dataset_id:
        d = await db.get(Dataset, m.dataset_id)
        if d:
            ds_name = d.name

    return {
        "id": m.id,
        "name": m.name,
        "base_model": m.base_model,
        "dataset_id": m.dataset_id,
        "dataset_name": ds_name,
        # v2.5.16: 任务类型透出 (与 list_models 对齐)
        "task_type": m.task_type,
        "num_classes": m.num_classes,
        "accuracy": float(m.accuracy or 0),
        "precision": float(m.precision or 0),
        "recall": float(m.recall or 0),
        "f1_score": float(m.f1_score or 0),
        # 检测专属指标
        "map_50": float(m.map_50) if m.map_50 is not None else None,
        "map_50_95": float(m.map_50_95) if m.map_50_95 is not None else None,
        # 分割专属指标
        "miou": float(m.miou) if m.miou is not None else None,
        "pixel_accuracy": float(m.pixel_accuracy) if m.pixel_accuracy is not None else None,
        "dice_score": float(m.dice_score) if m.dice_score is not None else None,
        "is_active": m.is_active,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "training_log": m.training_log,
        "confusion_matrix": m.confusion_matrix,
        "file_path": m.file_path,
    }


@router.delete("/{model_id}")
async def delete_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    删除指定模型版本。
    - v2 改造: 允许删除当前已激活的版本 (删除即取消激活)
    - 解绑 training_jobs.model_version_id 引用（保留训练历史，不级联删除）。
    - 尝试删除磁盘上的权重文件（仅在路径指向 models/ 目录时执行，避免误删）。
    """
    m = await db.get(ModelVersion, model_id)
    if not m:
        raise HTTPException(404, "Model not found")

    was_active = m.is_active

    # 解绑 training_jobs 上的引用（保留历史记录）
    try:
        await db.execute(
            update(TrainingJob)
            .where(TrainingJob.model_version_id == model_id)
            .values(model_version_id=None)
        )
    except Exception as e:
        await db.rollback()
        logger.warning("Failed to unlink training_jobs for model %s: %s", model_id, e)

    try:
        result = await _delete_one_model(db, m)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, f"Failed to delete model: {e}")

    return {
        "success": True,
        "deleted_id": model_id,
        "deleted_file": result["deleted_file"],
        "was_active": was_active,
    }


@router.post("/batch-delete")
async def batch_delete_models(
    body: BatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量删除模型版本.
    - 单个事务, 全部成功或全部回滚 (任一激活/不存在则全部拒绝).
    - 遵循与 DELETE /{model_id} 一致的语义:
        * 拒绝包含已激活版本
        * 解绑 training_jobs.model_version_id
        * 仅当路径在 models/ 下, 且无其他版本引用时才删权重文件
    - 返回:
        {
          success: bool,
          deleted_ids: [int],
          files_deleted: [int],
          detail: [{id, name, deleted_file}, ...]
        }
    """
    # 去重保持顺序
    seen = set()
    uniq_ids = []
    for i in body.ids:
        if i not in seen:
            seen.add(i)
            uniq_ids.append(i)

    # 1) 一次性查全部
    rows = (await db.execute(
        select(ModelVersion).where(ModelVersion.id.in_(uniq_ids))
    )).scalars().all()
    found_map = {m.id: m for m in rows}

    # 2) 校验: 缺失 (激活的不再拒绝, v2 改造: 删除即取消激活)
    missing = [i for i in uniq_ids if i not in found_map]
    if missing:
        raise HTTPException(404, f"模型版本不存在: {missing}")

    # 3) 解绑 training_jobs 引用
    try:
        await db.execute(
            update(TrainingJob)
            .where(TrainingJob.model_version_id.in_(uniq_ids))
            .values(model_version_id=None)
        )
    except Exception as e:
        await db.rollback()
        logger.warning("Failed to unlink training_jobs for batch %s: %s", uniq_ids, e)
        raise HTTPException(500, f"解绑训练任务失败: {e}")

    # 4) 逐个删除
    detail_list = []
    for m in rows:
        try:
            r = await _delete_one_model(db, m)
            detail_list.append({
                "id": r["id"],
                "name": m.name,
                "deleted_file": r["deleted_file"],
            })
        except Exception as e:
            await db.rollback()
            logger.error("Failed during batch delete at id=%s: %s", m.id, e)
            raise HTTPException(500, f"删除模型 id={m.id} 失败: {e}")

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, f"提交删除事务失败: {e}")

    return {
        "success": True,
        "deleted_ids": [d["id"] for d in detail_list],
        "files_deleted": sum(1 for d in detail_list if d["deleted_file"]),
        "detail": detail_list,
    }
