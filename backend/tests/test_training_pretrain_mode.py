"""
test_training_pretrain_mode
============================

校验 v3.0.0 新增的「训练模式 (pretrain_mode) + 来源 ModelVersion」追溯能力:

覆盖:
- pretrain_mode 决策纯函数 (start_training / restart / resume 三个入口)
- TrainingJob ORM 字段已加 (从源码校验)
- TrainingJobOut schema 暴露 3 字段
- start.py restart 入口根据 pretrained_source_mv_id 决定 pretrain_mode
- start.py resume 入口写 pretrain_mode=resume
- training_service._create_pending_job 支持 pretrain_mode 参数
- 前端 pretrainMode.ts 工具函数映射正确

策略: 纯函数 + 源码 contract 校验, 不依赖 DB/网络,
与 test_retrain_uses_row_mv.py 风格一致.
"""

# ============== 复制的纯函数 (与 start.py / training_service.py 同源) ==============

# 决定 pretrain_mode 的纯函数 (从 start_training 抽出)
def decide_start_pretrain_mode(pretrained_model_path: str) -> str:
    """start_training 入口: 用户是否传了 .pth 路径

    - 非空: incremental (用户私有权重起点, 不在业务 MV 体系内)
    - 空:  from_scratch (timm ImageNet)
    """
    if pretrained_model_path and pretrained_model_path.strip():
        return "incremental"
    return "from_scratch"


# 决定 pretrain_mode 的纯函数 (从 start_existing_training_job restart 抽出)
def decide_restart_pretrain_mode(pretrained_source_mv_id) -> str:
    """start_existing restart 入口: 是否解析到有效来源 MV

    - 非空: incremental (基于该 MV 继续)
    - 空:  from_scratch (有 .pth 但被删, 或压根没有 MV, 都按随机/ImageNet)
    """
    if pretrained_source_mv_id:
        return "incremental"
    return "from_scratch"


# 决定 pretrain_mode 的纯函数 (从 start_existing_training_job resume 抽出)
def decide_resume_pretrain_mode(existing_mode) -> str:
    """start_existing resume 入口: 沿用旧 job 的 mode, 没有则标 resume

    - 旧 job 已有 pretrain_mode: 沿用
    - 旧 job 之前未记录 (NULL): 标 resume
    """
    if existing_mode:
        return existing_mode
    return "resume"


# ============== 测试 ==============

class TestStartTrainingPretrainMode:
    """start_training 入口的 pretrain_mode 决策"""

    def test_empty_pretrained_path_means_from_scratch(self):
        assert decide_start_pretrain_mode("") == "from_scratch"

    def test_whitespace_pretrained_path_means_from_scratch(self):
        assert decide_start_pretrain_mode("   ") == "from_scratch"

    def test_none_pretrained_path_means_from_scratch(self):
        # 防御: None 也归 from_scratch (防 caller 传 None)
        assert decide_start_pretrain_mode(None) == "from_scratch"

    def test_valid_pretrained_path_means_incremental(self):
        assert decide_start_pretrain_mode("/path/to/foo.pth") == "incremental"

    def test_pretrained_path_with_spaces_means_incremental(self):
        assert decide_start_pretrain_mode("  /path/to/foo.pth  ") == "incremental"


class TestRestartPretrainMode:
    """start_existing restart 入口的 pretrain_mode 决策"""

    def test_no_source_mv_means_from_scratch(self):
        assert decide_restart_pretrain_mode(None) == "from_scratch"

    def test_with_source_mv_means_incremental(self):
        assert decide_restart_pretrain_mode(42) == "incremental"

    def test_zero_mv_id_still_from_scratch(self):
        # 0 是无效 ID (数据库自增从 1 开始), 不应算 incremental
        assert decide_restart_pretrain_mode(0) == "from_scratch"


class TestResumePretrainMode:
    """start_existing resume 入口的 pretrain_mode 决策"""

    def test_no_existing_mode_set_resume(self):
        # 历史 job 没有 pretrain_mode, 这次 resume 应标 resume
        assert decide_resume_pretrain_mode(None) == "resume"

    def test_existing_incremental_kept(self):
        # 已是 incremental 的 job 重新 resume (很少见, 但保持一致)
        assert decide_resume_pretrain_mode("incremental") == "incremental"

    def test_existing_from_scratch_kept(self):
        assert decide_resume_pretrain_mode("from_scratch") == "from_scratch"

    def test_existing_resume_kept(self):
        assert decide_resume_pretrain_mode("resume") == "resume"


# ============== 源码 contract 校验 (防止回归) ==============

class TestSourceContract:
    """通过读源码确认 ORM / Schema / API 集成正确"""

    def test_training_job_orm_has_pretrain_fields(self):
        """TrainingJob 必须有 pretrain_mode + pretrain_source_mv_id"""
        from app.tasks.model.training_job import TrainingJob
        cols = {c.name for c in TrainingJob.__table__.columns}
        assert "pretrain_mode" in cols, "TrainingJob 缺 pretrain_mode 字段"
        assert "pretrain_source_mv_id" in cols, "TrainingJob 缺 pretrain_source_mv_id 字段"

    def test_training_job_orm_has_pretrain_constants(self):
        """TrainingJob 必须导出 PRETRAIN_MODE_* 常量"""
        from app.tasks.model import training_job
        for name in ("PRETRAIN_MODE_FROM_SCRATCH", "PRETRAIN_MODE_INCREMENTAL", "PRETRAIN_MODE_RESUME"):
            assert hasattr(training_job, name), f"TrainingJob 缺常量 {name}"
        assert training_job.PRETRAIN_MODE_FROM_SCRATCH == "from_scratch"
        assert training_job.PRETRAIN_MODE_INCREMENTAL == "incremental"
        assert training_job.PRETRAIN_MODE_RESUME == "resume"

    def test_pretrain_mode_labels_uses_finetune_terminology(self):
        """v3.0.0 改版: 中文 label 简化为「微调/增量/继续训练」(不再用「从头训练」)"""
        from app.tasks.model.training_job import (
            PRETRAIN_MODE_LABELS,
            PRETRAIN_MODE_FROM_SCRATCH,
            PRETRAIN_MODE_INCREMENTAL,
            PRETRAIN_MODE_RESUME,
        )
        assert PRETRAIN_MODE_LABELS[PRETRAIN_MODE_FROM_SCRATCH] == "微调"
        assert PRETRAIN_MODE_LABELS[PRETRAIN_MODE_INCREMENTAL] == "增量"
        assert PRETRAIN_MODE_LABELS[PRETRAIN_MODE_RESUME] == "继续训练"
        # 防御: 不要再出现旧的「从头训练」文案
        assert "从头训练" not in PRETRAIN_MODE_LABELS.values()

    def test_training_job_out_schema_has_pretrain_fields(self):
        """TrainingJobOut 必须暴露 pretrain_mode / pretrain_source_mv_id / pretrain_source_mv_name"""
        from app.schemas.training import TrainingJobOut
        fields = TrainingJobOut.model_fields
        for name in ("pretrain_mode", "pretrain_source_mv_id", "pretrain_source_mv_name"):
            assert name in fields, f"TrainingJobOut 缺字段 {name}"

    def test_training_service_create_pending_job_supports_pretrain(self):
        """TrainingService._create_pending_job 必须支持 pretrain_mode 参数"""
        from app.tasks.service import training_service
        import inspect
        sig = inspect.signature(training_service.TrainingService._create_pending_job)
        assert "pretrain_mode" in sig.parameters
        assert "pretrain_source_mv_id" in sig.parameters

    def test_start_existing_restart_writes_pretrain_mode(self):
        """start_existing_training_job restart 入口必须调用 _create_pending_restart_job 时传 pretrain_mode"""
        from app.tasks.api.training import start as start_mod
        src = Path(start_mod.__file__).read_text(encoding="utf-8")
        # _create_pending_restart_job 调用处应出现 pretrain_mode 关键字
        assert "pretrain_mode=restart_pretrain_mode" in src, (
            "start.py restart 入口未把 restart_pretrain_mode 传给 _create_pending_restart_job"
        )

    def test_start_existing_resume_writes_pretrain_mode(self):
        """start_existing_training_job resume 入口必须设置 job.pretrain_mode"""
        from app.tasks.api.training import start as start_mod
        src = Path(start_mod.__file__).read_text(encoding="utf-8")
        assert 'job.pretrain_mode = "resume"' in src, (
            "start.py resume 入口未设置 job.pretrain_mode"
        )

    def test_jobs_api_enriches_source_mv_name(self):
        """jobs.py list/detail 必须调用 _batch_fill_source_mv_names"""
        from app.tasks.api.training import jobs as jobs_mod
        src = Path(jobs_mod.__file__).read_text(encoding="utf-8")
        assert "_batch_fill_source_mv_names" in src, (
            "jobs.py 未实现/调用 _batch_fill_source_mv_names, 列表/详情会拿不到来源 MV 名称"
        )

    def test_migration_script_exists(self):
        """迁移脚本必须存在 (序号化: 05_add_training_pretrain_mode.py)"""
        from pathlib import Path as _P
        repo_root = _P(__file__).resolve().parents[1]
        mig = repo_root / "migrations" / "05_add_training_pretrain_mode.py"
        assert mig.exists(), f"迁移脚本缺失: {mig}"
        # 防御: 旧的非编号命名不应再出现 (避免脚本执行顺序混乱)
        old_mig = repo_root / "migrations" / "add_training_pretrain_mode.py"
        assert not old_mig.exists(), (
            f"检测到非编号命名的迁移脚本: {old_mig}, "
            "请改用 NN_xxx.py 两位数字序号命名以保证执行顺序"
        )

    def test_backfill_script_exists(self):
        """历史 NULL → from_scratch 回填脚本必须存在 (序号化: 06_backfill_pretrain_mode_finetune.py)"""
        from pathlib import Path as _P
        repo_root = _P(__file__).resolve().parents[1]
        backfill = repo_root / "migrations" / "06_backfill_pretrain_mode_finetune.py"
        assert backfill.exists(), f"历史回填脚本缺失: {backfill}"
        # 防御: 旧的非编号命名不应再出现
        old_backfill = repo_root / "migrations" / "backfill_pretrain_mode_finetune.py"
        assert not old_backfill.exists(), (
            f"检测到非编号命名的迁移脚本: {old_backfill}, "
            "请改用 NN_xxx.py 两位数字序号命名以保证执行顺序"
        )
        content = backfill.read_text(encoding="utf-8")
        # 关键 SQL: NULL → from_scratch
        assert "pretrain_mode IS NULL" in content
        assert "pretrain_mode = 'from_scratch'" in content
        # 必须有幂等保护 (避免重复执行改写其他模式)
        assert "WHERE pretrain_mode IS NULL" in content

    def test_frontend_pretrain_mode_util_exists(self):
        """前端 pretrainMode.ts 必须存在 + label 简化为「微调/增量/继续训练」"""
        from pathlib import Path as _P
        fe_util = _P(__file__).resolve().parents[2] / "frontend" / "src" / "utils" / "pretrainMode.ts"
        assert fe_util.exists(), f"前端工具缺失: {fe_util}"
        content = fe_util.read_text(encoding="utf-8")
        # v3.0.0 改版: 三个模式的中文 label 必须都是新文案
        assert "微调" in content, "pretrainMode.ts 缺「微调」label (from_scratch)"
        assert "增量" in content, "pretrainMode.ts 缺「增量」label (incremental)"
        assert "继续训练" in content, "pretrainMode.ts 缺「继续训练」label (resume)"
        # 防御: 不要再出现旧的「从头训练」文案
        assert "从头训练" not in content, "pretrainMode.ts 不应再使用「从头训练」文案"

    def test_frontend_detail_dialog_uses_pretrain_meta(self):
        """TrainingDetailDialog.vue 必须引用 getPretrainModeMeta"""
        from pathlib import Path as _P
        fe_dialog = _P(__file__).resolve().parents[2] / "frontend" / "src" / "views" / "Training" / "components" / "TrainingDetailDialog.vue"
        assert fe_dialog.exists()
        content = fe_dialog.read_text(encoding="utf-8")
        assert "getPretrainModeMeta" in content, "TrainingDetailDialog.vue 未引用 getPretrainModeMeta"
        assert "pretrain_source_mv_id" in content, "TrainingDetailDialog.vue 未展示来源 MV"

    def test_frontend_jobs_table_uses_pretrain_meta(self):
        """TrainingJobsTable.vue 必须引用 getPretrainModeMeta"""
        from pathlib import Path as _P
        fe_table = _P(__file__).resolve().parents[2] / "frontend" / "src" / "views" / "Training" / "components" / "TrainingJobsTable.vue"
        assert fe_table.exists()
        content = fe_table.read_text(encoding="utf-8")
        assert "getPretrainModeMeta" in content, "TrainingJobsTable.vue 未引用 getPretrainModeMeta"
        assert "训练模式" in content, "TrainingJobsTable.vue 缺「训练模式」列"


# ============== 必填 import ==============
from pathlib import Path
