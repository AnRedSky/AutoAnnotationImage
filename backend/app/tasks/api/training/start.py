"""
training.start 模块 — 训练任务启动 (新建 + 复用旧任务)
=====================================================

**v3.0.0 Phase O 拆分**: 从 training.py 抽离
**职责**:
- start_training: 全新训练 (POST /api/training/start)
- start_existing_training_job: 复用旧任务 (POST /api/training/jobs/{id}/start, mode=restart|resume)

**共享工具**:
- _build_task_kwargs: 按 task_type 构建 Celery task kwargs
- _apply_training_task: 按 task_type 投递到对应 Celery 任务
- _create_pending_restart_job: 预创建 PENDING 行 (避免 worker 启动前 GET /jobs 查不到)
- _rollback_restart: 预创建失败回滚

**Phase 3 业务下沉**: start_training 已 thin wrapper, 业务逻辑 (broker 校验/失败回滚)
全部在 TrainingService.start_training.
"""
from datetime import datetime
from pathlib import Path
import random
import re
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.admin.model.user import User
from app.middleware.http.auth import get_current_user
from app.tasks.model.training_job import TrainingJob
from app.tasks.model.model_version import ModelVersion
from app.tasks.workers.classification import train_model_task
from app.schemas.training import (
    TrainStartResponse,
    TrainingJobActionResult,
    TrainingJobUpdate,
)
from app.tasks.service.training_service import TrainingService

router = APIRouter()


# ============== 共享工具 ==============

# model_name 字段最大长度 (与 TrainingJob.model_name / ModelVersion.name 字段定义一致)
MODEL_NAME_MAX_LEN = 128
# 再训练 _r_{timestamp} 后缀长度: "_r_" (3) + 10位秒级时间戳 (10) = 13
RETRAIN_SUFFIX_LEN = 13
# 查重时的随机后缀长度: "_" (1) + 3位随机字符 (3) = 4
UNIQUE_RANDOM_SUFFIX_LEN = 4
# 查重最大尝试次数 (避免极端情况死循环)
UNIQUE_RANDOM_MAX_TRY = 16
# 查重时使用的随机字符集 (数字 0-9, 排除易混淆字母)
UNIQUE_RANDOM_CHARS = "0123456789"


def _strip_retrain_suffix(name: str) -> str:
    """剥离末尾的 _r_{10位数字} 后缀 (再训练时反复加, 避免 name 无限增长)

    兼容旧版 _r{10位数字} (无下划线分隔) + 新版 _r_{10位数字} (有下划线分隔),
    严格按 _r_?{digits}$ 匹配, 避免误伤业务字段里的 _r (如 'resnet_r2.0')
    """
    return re.sub(r"_r_?\d+$", "", name)


def _default_model_name(base_model: str, *, retrain: bool = False) -> str:
    """生成默认 model_name (新建/再训练统一入口)

    规则 (v3.0.0 Phase 升级):
      - 新建任务: `{base_model}_{timestamp}` (e.g. resnet50_1701234567)
      - 再训练任务: `{base_model}_r_{timestamp}` (e.g. resnet50_r_1701234567)
      - 时间戳: 10位秒级 (避免 13位毫秒太长, 也保证一年内不重复)

    Args:
        base_model: 基础模型名 (e.g. resnet50)
        retrain: True 表示再训练, False 表示新建

    Returns:
        不带查重的默认名称 (调用方应再调 _resolve_unique_model_name 兜底)
    """
    ts = int(datetime.utcnow().timestamp()) % 10000000000
    suffix = f"_r_{ts}" if retrain else f"_{ts}"
    return f"{base_model}{suffix}"


def _make_random_suffix() -> str:
    """生成 3 位数字随机后缀 (用于名称查重时拼接)

    Returns:
        形如 '_847' 的随机后缀 (含前导下划线)
    """
    return f"_{''.join(random.choices(UNIQUE_RANDOM_CHARS, k=3))}"


def _resolve_unique_model_name(
    candidate: str,
    exists_check,
    *,
    max_len: int = MODEL_NAME_MAX_LEN,
    max_try: int = UNIQUE_RANDOM_MAX_TRY,
) -> str:
    """解决 model_name 重复 (在候选名基础上加随机三位数后缀)

    策略:
      1) 检查 candidate 是否已存在
      2) 不存在 → 返回 candidate
      3) 存在 → 在末尾追加 3 位数字 (e.g. _847), 再次检查
      4) 最多尝试 max_try 次 (默认 16), 仍冲突 → 强制返回 (带 3 位随机)
         实际场景中 base_model + ts 已经几乎唯一, 3-4 次内必收敛

    Args:
        candidate: 候选名 (e.g. resnet50_1701234567)
        exists_check: 同步回调, 输入 name 返回 bool (True=已存在)
        max_len: 字段最大长度 (默认 128)
        max_try: 最大尝试次数 (默认 16)

    Returns:
        唯一名 (若超长会先截断 base, 保留后缀)
    """
    if not candidate or not exists_check(candidate):
        return candidate
    # 截断 base 部分, 给随机后缀留位 (4 字符)
    base_max = max_len - UNIQUE_RANDOM_SUFFIX_LEN
    base = candidate[:base_max] if len(candidate) > base_max else candidate
    for _ in range(max_try):
        rand = _make_random_suffix()
        cand = f"{base}{rand}"
        if not exists_check(cand):
            return cand
    # 兜底: 仍冲突, 直接返回最后一次 (极端情况, 用户应手动改名)
    return f"{base}{_make_random_suffix()}"


async def _resolve_unique_model_name_async(
    candidate: str,
    exists_check,
    *,
    max_len: int = MODEL_NAME_MAX_LEN,
    max_try: int = UNIQUE_RANDOM_MAX_TRY,
) -> str:
    """异步版 _resolve_unique_model_name (适配 AsyncSession 场景)

    Args:
        candidate: 候选名 (e.g. resnet50_1701234567)
        exists_check: 异步回调, 输入 name 返回 bool (True=已存在)
        max_len: 字段最大长度 (默认 128)
        max_try: 最大尝试次数 (默认 16)

    Returns:
        唯一名 (若超长会先截断 base, 保留后缀)
    """
    if not candidate or not await exists_check(candidate):
        return candidate
    # 截断 base 部分, 给随机后缀留位 (4 字符)
    base_max = max_len - UNIQUE_RANDOM_SUFFIX_LEN
    base = candidate[:base_max] if len(candidate) > base_max else candidate
    for _ in range(max_try):
        rand = _make_random_suffix()
        cand = f"{base}{rand}"
        if not await exists_check(cand):
            return cand
    # 兜底: 仍冲突, 直接返回最后一次 (极端情况, 用户应手动改名)
    return f"{base}{_make_random_suffix()}"


def _safe_truncate_model_name(name: str, max_len: int = MODEL_NAME_MAX_LEN) -> str:
    """保证 model_name 长度不超过 max_len, 保护 DB 写库不报 DataError

    - 已超长: 从左侧截断到 max_len (会破坏 _r{ts} 后缀的, 单独处理)
    - 对于再训练场景, 保留 _r{ts} 后缀的语义, 应该用 _build_retrain_model_name
    - 纯输入截断 (无后缀追加), 用于 start_training 路径

    Args:
        name: 原始 model_name
        max_len: 字段最大长度 (默认 128)

    Returns:
        截断后的 model_name (不超过 max_len)
    """
    if not name:
        return name
    if len(name) <= max_len:
        return name
    return name[:max_len]


def _build_retrain_model_name(
    src_name: str,
    max_len: int = MODEL_NAME_MAX_LEN,
    suffix: str = "",
) -> str:
    """构造再训练后的 model_name, 始终保留 _r{ts} 后缀保证 .pth 文件名唯一

    策略:
      1) 剥离 src_name 末尾的 _r{digits} 后缀 (避免重复)
      2) 计算 base_name 可用空间 = max_len - len(suffix)
      3) 从左侧截断 base_name 到可用空间
      4) 拼接 = base_name + suffix

    Args:
        src_name: 原 model_name (可能已经带 _r{ts} 后缀)
        max_len: 字段最大长度 (默认 128)
        suffix: 要追加的 _r{ts} 后缀 (如 '_r1701234567'), 默认空

    Returns:
        不超 max_len 的新 model_name
    """
    base = _strip_retrain_suffix(src_name)
    available = max_len - len(suffix)
    if len(base) > available:
        base = base[:available]
    return f"{base}{suffix}"


def _build_task_kwargs(
    task_type: str,
    *,
    dataset_id: int,
    user_id: int,
    base_model: str,
    model_name: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    pretrained_model_path: Optional[str] = None,
) -> dict:
    """按 task_type 构建对应 Celery 任务的 kwargs.

    - classification: timm 微调 (base_model=..., model_name=别名, 支持增量权重)
    - detection:     ultralytics YOLO (model_name=权重名 yolov8n/..., model_alias=落盘名)
    - segmentation:   torchvision DeepLabV3+ (backbone=..., model_alias=落盘名)

    detection/segmentation 的 task 签名不接受 pretrained_model_path
    (YOLO 用 ultralytics 自带预训练权重, DeepLab 用 torchvision 预训练), 故忽略.
    """
    if task_type == "detection":
        return dict(
            dataset_id=dataset_id,
            user_id=user_id,
            model_name=base_model,    # yolov8n/s/m/l/x
            model_alias=model_name,  # 落盘 ModelVersion.name
            epochs=epochs,
            batch=batch_size,
        )
    if task_type == "segmentation":
        return dict(
            dataset_id=dataset_id,
            user_id=user_id,
            backbone=base_model,     # deeplabv3_resnet50/101
            model_alias=model_name,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
        )
    # classification (默认)
    return dict(
        dataset_id=dataset_id,
        base_model=base_model,
        model_name=model_name,
        user_id=user_id,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        pretrained_model_path=pretrained_model_path if pretrained_model_path else None,
    )


def _apply_training_task(task_type: str, kwargs: dict, *, task_id: Optional[str] = None):
    """按 task_type 选择 Celery 任务并投递.

    - task_id 非空: apply_async(task_id=...) 强制使用预生成 ID (预创建行场景)
    - task_id 为空: delay() 让 Celery 自动生成 ID (resume 复用旧 job 场景,
      随后由调用方把 job.celery_task_id 更新为 task.id)
    """
    if task_type == "detection":
        from app.tasks.workers.detection import train_detection_task
        task = train_detection_task
    elif task_type == "segmentation":
        from app.tasks.workers.segmentation import train_segmentation_task
        task = train_segmentation_task
    else:
        task = train_model_task
    if task_id:
        return task.apply_async(kwargs=kwargs, task_id=task_id)
    return task.delay(**kwargs)


async def _create_pending_restart_job(
    *,
    celery_task_id: str,
    user_id: int,
    dataset_id: int,
    base_model: str,
    model_name: str,
    task_type: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    pretrain_mode: str,
    pretrain_source_mv_id: Optional[int] = None,
) -> int | None:
    """预创建 PENDING 行 (mode=restart)
    - 与 start_training 不同: 显式传 started_at=None, 避免 SQLAlchemy 自动填
      default=datetime.utcnow (这会让 PENDING 阶段就显示"已开始")
    - 容错: 如果 inserted_primary_key 拿不到 (极端情况), 返回 None
    - 重试: aiomysql 偶发 MySQLServerHasGoneAway 时, 一次重试, 仍失败则抛
    - v3.0.0: 写入 pretrain_mode + pretrain_source_mv_id, 用于详情页追溯
      (restart 场景下, 这两个值由 start_existing_training_job 在创建前解析出来)

    注意: 必须写成 async def, 直接 await _do().
    早期写成 def + _run_async(_do()) 在 async 上下文里会触发
    "Cannot run the event loop while another loop is running"
    """
    async def _do():
        async with AsyncSessionLocal() as sdb:
            result = await sdb.execute(
                mysql_insert(TrainingJob).values(
                    celery_task_id=celery_task_id,
                    user_id=user_id,
                    dataset_id=dataset_id,
                    base_model=base_model,
                    model_name=model_name,
                    task_type=task_type,
                    epochs=epochs,
                    batch_size=batch_size,
                    learning_rate=learning_rate,
                    pretrain_mode=pretrain_mode,
                    pretrain_source_mv_id=pretrain_source_mv_id,
                    state="PENDING",
                    progress=0.0,
                    message="等待 worker 启动...",
                    created_at=datetime.utcnow(),
                    started_at=None,
                    finished_at=None,
                )
            )
            await sdb.commit()
            pk = result.inserted_primary_key
            return pk[0] if pk else None
    try:
        return await _do()
    except Exception:
        # 重试一次 (偶发 MySQLServerHasGoneAway / 连接池抖动)
        return await _do()


async def _rollback_restart(new_job_id: int) -> None:
    """删除预创建的行 (apply_async 失败时调用)"""
    async with AsyncSessionLocal() as sdb:
        await sdb.execute(
            delete(TrainingJob).where(TrainingJob.id == new_job_id)
        )
        await sdb.commit()


# ============== 路由 ==============

@router.post("/start", response_model=TrainStartResponse)
async def start_training(
    dataset_id: int,
    base_model: str = "efficientnet_b0",
    model_name: str = "",
    epochs: int = 20,
    batch_size: int = 32,
    learning_rate: float = 1e-4,
    pretrained_model_path: str = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    启动异步训练任务 (v3.0.0 Phase 3: thin wrapper, 业务下沉到 TrainingService)

    - 提交到 Celery worker
    - 返回 task_id 供前端轮询
    - model_name 兜底 / broker 校验 / 预创建行 / 失败回滚 全部在 TrainingService

    pretrained_model_path: 增量训练 (再训练) 时, 传入 .pth 文件路径作为模型起点
    - 空字符串 (默认): 从头微调 (timm ImageNet 预训练权重)
    - 已有路径: 加载该 .pth 的 state_dict (fine-tune 旧模型)
    """
    # P0-5: 非 admin 只能对自己的 dataset 启训练
    from app.tasks.model.dataset import Dataset
    dataset = await db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    from app.tasks.service.permission_service import assert_can_access_dataset
    await assert_can_access_dataset(db, current_user, dataset, require_write=True)

    result = await TrainingService.start_training(
        db,
        user_id=current_user.id,
        dataset_id=dataset_id,
        base_model=base_model,
        model_name=model_name,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        pretrained_model_path=pretrained_model_path,
    )

    # MT-8: 审计日志
    from app.tasks.service.audit_service import log_audit
    await log_audit(db, user_id=current_user.id, event_type="training_started",
                    resource_type="training_job", resource_id=result.get("job_id"),
                    detail={"dataset_id": dataset_id, "base_model": base_model,
                            "epochs": epochs, "model_name": model_name})
    await db.commit()

    return TrainStartResponse(**result)


@router.post("/jobs/{job_id}/start", response_model=TrainingJobActionResult)
async def start_existing_training_job(
    job_id: int,
    mode: str = Query(default="restart", description="restart=再训练 (新 job_id) | resume=继续 (复用同 job, 不改 model_name)"),
    payload: Optional[TrainingJobUpdate] = Body(default=None, description="参数覆盖 (仅 mode=restart 生效, mode=resume 必须用原 job 参数)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """复用旧任务配置重新提交 Celery 训练任务

    - mode=restart (默认, 用于终态任务的「再训练」按钮):
        * 用旧 job 的 (dataset_id, base_model, model_name, epochs, batch_size, learning_rate)
        * 可选 payload 参数覆盖 (再训练弹窗): 用于「原任务不动, 仅作为新任务的训练参数」场景
        * 新 model_name = 原 model_name + _r{timestamp} 后缀 (避免 .pth 文件冲突)
        * 旧 job 记录保持不变, worker 接手时写新 PENDING 记录
    - mode=resume (用于 PAUSED 任务的「继续」按钮):
        * 复用原 job 的全部配置, 包括 model_name (覆盖现有 .pth 即可)
        * 旧 job 记录继续使用, worker 重新执行训练
        * 不允许传 payload (语义: 继续 = 断点续训, 不能换参数)
    - 若旧 job 仍在 PENDING/PROGRESS, 返回错误
    """
    if mode not in ("restart", "resume"):
        raise HTTPException(400, f"invalid mode: {mode}, must be 'restart' or 'resume'")

    job = await db.get(TrainingJob, job_id)
    if not job:
        raise HTTPException(404, "Training job not found")
    if job.state in ("PENDING", "PROGRESS"):
        return TrainingJobActionResult(
            success=False,
            job_id=job_id,
            state=job.state,
            message=f"Job is still running in state {job.state}. Cancel it first.",
        )

    # resume 模式要求任务处于 PAUSED 状态 (其他终态走 restart)
    if mode == "resume" and job.state != "PAUSED":
        return TrainingJobActionResult(
            success=False,
            job_id=job_id,
            state=job.state,
            message=f"Only PAUSED job can be resumed; current state is {job.state}",
        )

    # ---- 计算最终训练参数 (旧 job 字段为基底, payload 覆盖) ----
    overrides = payload.model_dump(exclude_unset=True) if payload else {}

    if mode == "resume":
        if overrides:
            raise HTTPException(
                400,
                "resume 模式不允许修改参数 (继续训练 = 断点续训). 如需新参数, 请用「再训练」"
            )
        final_dataset_id = job.dataset_id
        final_base_model = job.base_model
        final_epochs = job.epochs
        final_batch_size = job.batch_size
        final_learning_rate = job.learning_rate
        new_model_name = job.model_name
        final_task_type = (job.task_type or "classification").lower()
    else:
        # restart: 应用 payload 覆盖 (不修改 job 记录)
        final_dataset_id = overrides.get("dataset_id", job.dataset_id)
        final_base_model = overrides.get("base_model", job.base_model)
        final_epochs = overrides.get("epochs", job.epochs)
        final_batch_size = overrides.get("batch_size", job.batch_size)
        final_learning_rate = overrides.get("learning_rate", job.learning_rate)
        final_task_type = (job.task_type or "classification").lower()
        # model_name: 强制使用 `{base_model}_r_{ts}` 规则 (忽略 payload 的 model_name)
        # v3.0.0 重构: 不再复用 payload 里的 model_name, 一律重置为
        #   `{base_model}_r_{10位秒级ts}`
        # 原因: 旧版会出现以下两个问题
        #   1) 后缀重复累加: resnet50_v1_r123_r456 → 越来越长
        #   2) 后缀与 base_model 混淆: 改名时容易拼出非法字符
        # 查重: 同一 dataset 下重名时, 自动加 3 位随机数字后缀 (e.g. _847)
        from sqlalchemy import and_ as _and, exists as _sa_exists
        async def _exists(name: str) -> bool:
            # 异步查询: 该 name 在 dataset 下是否已存在 (含已删除/历史的所有 job)
            stmt = select(_sa_exists().where(
                _and(
                    TrainingJob.dataset_id == final_dataset_id,
                    TrainingJob.model_name == name,
                )
            ))
            return bool((await db.execute(stmt)).scalar())

        candidate = _default_model_name(final_base_model, retrain=True)
        new_model_name = await _resolve_unique_model_name_async(
            candidate, _exists
        )

    # ---- 增量训练 (再训练) 核心: 基于「当前所在行」的 ModelVersion ----
    # 用户在 N 行点「再训练」, 不管该 MV 是否 is_active, 都基于它继续训练
    # (语义对齐: "再训练" = 在该任务产出的模型基础上继续)
    #
    # 解析顺序:
    #   1) job.model_version_id 非空 → 直接用该 MV (无论 is_active)
    #   2) 否则查 dataset 下 is_active=True 的最新 MV (兼容老 job 训练失败没产 MV 的场景)
    #   3) 都没有 → None (run_training 走 timm ImageNet 预训练)
    pretrained_model_path = None
    pretrained_source_mv_id = None
    pretrained_source_label = "无 (从头微调)"
    if mode == "restart":
        candidate_mv = None
        # 1) 优先: 当前行关联的 ModelVersion
        if job.model_version_id:
            candidate_mv = await db.get(ModelVersion, job.model_version_id)
            if candidate_mv:
                pretrained_source_label = (
                    f"当前行 ModelVersion #{candidate_mv.id} "
                    f"(name={candidate_mv.name!r}, active={candidate_mv.is_active})"
                )
        # 2) 回退: 数据集下激活的最新 ModelVersion
        if candidate_mv is None:
            active_mv = (await db.execute(
                select(ModelVersion)
                .where(
                    ModelVersion.dataset_id == final_dataset_id,
                    ModelVersion.is_active == True,  # noqa: E712
                )
                .order_by(ModelVersion.id.desc())
            )).scalars().first()
            if active_mv:
                candidate_mv = active_mv
                pretrained_source_label = (
                    f"激活 ModelVersion #{active_mv.id} (name={active_mv.name!r})"
                )

        # 取 .pth 路径, 文件存在才算有效
        if candidate_mv and candidate_mv.file_path:
            pth = Path(candidate_mv.file_path)
            if pth.exists():
                pretrained_model_path = str(pth)
                pretrained_source_mv_id = candidate_mv.id
            else:
                pretrained_source_label += " [文件不存在, 改从头微调]"

    # ---- 预创建 TrainingJob 行 (mode=restart) ----
    new_job_id: int | None = None
    celery_task_id_to_use: str | None = None

    if mode == "restart":
        celery_task_id_to_use = uuid4().hex
        # 决定 pretrain_mode: 有有效来源 MV → incremental, 否则 from_scratch
        # (有 .pth 但文件被删, 也归 from_scratch: 实际训练是随机的)
        restart_pretrain_mode = (
            "incremental" if pretrained_source_mv_id else "from_scratch"
        )
        new_job_id = await _create_pending_restart_job(
            celery_task_id=celery_task_id_to_use,
            user_id=current_user.id,
            dataset_id=final_dataset_id,
            base_model=final_base_model,
            model_name=new_model_name,
            task_type=final_task_type,
            epochs=final_epochs,
            batch_size=final_batch_size,
            learning_rate=final_learning_rate,
            pretrain_mode=restart_pretrain_mode,
            pretrain_source_mv_id=pretrained_source_mv_id,
        )
        if new_job_id is None:
            # 防御性兜底: 预创建返回 None, 直接 500
            raise HTTPException(500, "预创建训练任务行失败 (inserted_primary_key 为空), 请重试")

    try:
        apply_kwargs = _build_task_kwargs(
            final_task_type,
            dataset_id=final_dataset_id,
            user_id=current_user.id,
            base_model=final_base_model,
            model_name=new_model_name,
            epochs=final_epochs,
            batch_size=final_batch_size,
            learning_rate=final_learning_rate,
            pretrained_model_path=pretrained_model_path,
        )
        if mode == "restart":
            task = _apply_training_task(final_task_type, apply_kwargs, task_id=celery_task_id_to_use)
        else:
            task = _apply_training_task(final_task_type, apply_kwargs)
    except Exception as e:
        # 入队失败, 回滚预创建的行
        if new_job_id is not None:
            try:
                await _rollback_restart(new_job_id)
            except Exception:
                pass
        err_msg = str(e)[:200]
        if any(k in err_msg.lower() for k in ["connection", "refused", "redis", "broker"]):
            raise HTTPException(503, f"Celery broker unavailable: {err_msg}")
        raise HTTPException(500, f"Failed to submit training task: {err_msg}")

    # resume 模式: 立刻把 DB 中的 job 状态从 PAUSED 改回 PENDING
    if mode == "resume":
        job.state = "PENDING"
        job.progress = 0.0
        job.celery_task_id = task.id
        # started_at 保留 None, 由 worker 接手时填充
        # job.started_at 维持原值, 因为语义上是"任务整体首次开始"的时间
        job.finished_at = None
        job.error = None
        # v3.0.0: resume 模式标记 pretrain_mode, 用于详情页追溯
        # (同一 job 重新执行, 模型起点不变, 模式仍是 "resume", 避免被识别为新的 incremental)
        if not job.pretrain_mode:
            job.pretrain_mode = "resume"
        await db.commit()
        await db.refresh(job)

    return TrainingJobActionResult(
        success=True,
        job_id=job_id,
        new_job_id=new_job_id,
        state="PENDING",
        message=(
            f"Resumed training (model_name={new_model_name})"
            if mode == "resume"
            else (
                f"New training task #{new_job_id} created (model_name={new_model_name}, "
                f"原任务 #{job_id} 保持不变, 增量训练来源: {pretrained_source_label})"
            )
        ),
        task_id=task.id,
    )
