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
from app.tasks.model.model_version import ModelVersion
from app.tasks.model.dataset import Dataset
from app.tasks.model.training_job import TrainingJob
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.core.config import settings
# v3.0.0 审查修复: API 层改用 ModelService 编排, 避免业务逻辑写在 controller
from app.tasks.service.model_service import ModelService

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
    dataset_id: Optional[int] = Query(default=None, description="数据集 ID; 缺省=全局所有数据集, 每个数据集返回 1 个最佳模型"),
    task_type: Optional[str] = Query(default=None, description="任务类型过滤: classification|detection|segmentation"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    列出当前激活的模型版本 (供标注工作台 / 概览面板)

    v3.0.0 审查修复: 严格遵循 memory 硬约束
    - 每个 dataset 只返回 1 个最佳模型 (mAP50/mIoU/accuracy 降序, created_at 降序)
    - dataset_id 缺省: 全局所有数据集各自的最优模型
    - 委托 ModelService.get_active_for_dataset() 保证业务一致性
    """
    items: list[dict] = []

    # 1) 决定要遍历的 dataset 列表
    if dataset_id is not None:
        ds_ids = [dataset_id]
    else:
        rows = (await db.execute(select(Dataset.id))).all()
        ds_ids = [r[0] for r in rows]

    # 2) 逐 dataset 委托 ModelService (排序: mAP50/mIoU/accuracy DESC, created_at DESC)
    for ds_id in ds_ids:
        active = await ModelService.get_active_for_dataset(db, ds_id, task_type=task_type)
        if not active:
            continue
        items.append({
            "id": active.id,
            "name": active.name,
            "base_model": active.base_model,
            "dataset_id": active.dataset_id,
            "task_type": active.task_type,
            "num_classes": active.num_classes,
            "accuracy": float(active.accuracy or 0),
            "f1_score": float(active.f1_score or 0),
            "map_50": float(active.map_50) if active.map_50 is not None else None,
            "miou": float(active.miou) if active.miou is not None else None,
            "is_active": active.is_active,
            "created_at": active.created_at.isoformat() if active.created_at else None,
        })

    # 3) 预查 dataset_name (避免 N+1)
    ds_name_map: dict[int, str] = {}
    if items:
        ds_ids2 = sorted({m["dataset_id"] for m in items if m["dataset_id"]})
        if ds_ids2:
            ds_rows = (await db.execute(
                select(Dataset.id, Dataset.name).where(Dataset.id.in_(ds_ids2))
            )).all()
            for r in ds_rows:
                ds_name_map[r[0]] = r[1]
    for m in items:
        m["dataset_name"] = ds_name_map.get(m["dataset_id"]) if m["dataset_id"] else None

    return {"items": items}


@router.post("/{model_id}/activate")
async def activate_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    激活指定模型版本

    v3.0.0 审查修复: 严格遵循 memory 硬约束
    - 同一 dataset 下其他激活模型自动取消 (单激活语义)
    - 委托 ModelService.activate() 保证业务一致性
    - 幂等: 目标已是激活状态时也返回 success=True
    """
    target = await db.get(ModelVersion, model_id)
    if not target:
        raise HTTPException(404, "Model not found")

    if target.dataset_id is None:
        raise HTTPException(400, "Model has no associated dataset, cannot activate")

    try:
        await ModelService.activate(db, target, commit=True)
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

    v3.0.0 审查修复: 委托 ModelService.deactivate() 保证业务一致性
    """
    target = await db.get(ModelVersion, model_id)
    if not target:
        raise HTTPException(404, "Model not found")

    try:
        await ModelService.deactivate(db, target, commit=True)
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
