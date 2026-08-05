"""
model.deletion 模块 — 模型版本删除 API
======================================

**v3.0.0 Phase R 拆分**: 从 model.py 抽离
**职责**: 单个/批量删除 + 权重文件清理 + training_job 解绑

**路由清单** (2 个):
- DELETE /{model_id}            删除单个模型版本
- POST   /batch-delete          批量删除模型版本 (同一事务, 全部成功或全部回滚)

**删除语义** (v2 改造):
- 允许删除当前已激活的版本 (删除即取消激活)
- 解绑 training_jobs.model_version_id 引用 (保留训练历史, 不级联删除)
- 仅当路径在 models/ 目录下, 且无其他版本引用时才删权重文件 (避免误删)
- 批量操作原子性: 任何一个失败则全部回滚

**S5 文件删除约定**:
- 仅在 `abs_path.startswith(models_dir + os.sep)` 时才执行 `os.remove`
- 防止误删用户在文件系统任意位置引用的模型
- 多版本共享同一 file_path 时, 只删最后一个引用, 保留文件

**S9 training_job 解绑**:
- `update(TrainingJob).where(model_version_id == id).values(model_version_id=NULL)`
- 保留 TrainingJob 历史记录 (job_id / status / metrics / log 全部可追溯)
- 仅解除与已删除 ModelVersion 的外键引用
"""
import logging
import os
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.model.model_version import ModelVersion
from app.tasks.model.dataset import Dataset
from app.tasks.model.training_job import TrainingJob
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.core.config import settings
from app.tasks.service.permission_service import assert_can_access_dataset

logger = logging.getLogger(__name__)
router = APIRouter()


# ============== Schemas ==============

class BatchDeleteRequest(BaseModel):
    ids: List[int] = Field(..., min_length=1, max_length=200,
                           description="待删除的模型版本 id 列表, 数量 1-200")


# ============== 内部工具 ==============

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


# ============== 单个删除 ==============

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

    v3.3.0 P0 修复: 必须校验写权限
    v3.3.5-PERMISSION-REWRITE: 孤儿 model 任何角色均拒绝
      (含 super_admin, 不再旁路; 需先迁移到有效 dataset 才能删除)
    """
    from app.tasks.service.permission_service import assert_can_access_model
    m = await db.get(ModelVersion, model_id)
    if not m:
        raise HTTPException(404, "Model not found")

    # v3.3.5: 统一通过 assert_can_access_model 校验, 孤儿 model 任何角色拒绝
    await assert_can_access_model(db, current_user, m.dataset_id, require_write=True)

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


# ============== 批量删除 ==============

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

    # 1.5) 权限校验: 任一 model 所属 dataset 不可写则整体拒绝 (v3.3.0 P0 修复)
    # v3.3.5-PERMISSION-REWRITE: 孤儿 model 任何角色都拒绝
    from app.tasks.service.permission_service import assert_can_access_model
    for m in rows:
        await assert_can_access_model(db, current_user, m.dataset_id, require_write=True)

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
