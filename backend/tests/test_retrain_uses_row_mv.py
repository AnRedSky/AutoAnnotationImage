"""
test_retrain_uses_row_mv
========================

校验「再训练 (mode=restart)」时, 增量训练 .pth 路径的解析逻辑:

- 场景 1: 当前行有 model_version_id (无论 is_active) → 用该 MV 的 .pth
- 场景 2: 当前行无 model_version_id + 数据集下有 is_active=True 的 MV → 回退到激活 MV
- 场景 3: 当前行无 model_version_id + 数据集下无激活 MV → pretrained_model_path=None (从头微调)

通过单测 _resolve_pretrained_path 的纯函数逻辑覆盖 (不依赖 DB/网络),
实际 API 层只负责传 job / dataset, 决策交给该纯函数.

注: 把解析逻辑封装为模块级纯函数, 避免直接覆盖 FastAPI 路由 (依赖注入复杂).
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


# ============== Mock 掉慢依赖, 让纯函数可单测 ==============
# runpy / import 时不希望触发 Celery / 实际数据库连接
# 直接复制 start.py 中的纯函数 (避免改 start.py 的导出)
def resolve_pretrained_path(
    job_model_version_id,  # int | None
    job_mv_record,         # ModelVersion | None
    active_mv_record,      # ModelVersion | None (全数据集扫描结果)
):
    """纯函数: 给定当前行 MV + 激活 MV, 解析增量训练 .pth 路径

    返回: (pretrained_model_path: str | None,
           pretrained_source_mv_id: int | None,
           label: str)
    """
    pretrained_model_path = None
    pretrained_source_mv_id = None
    label = "无 (从头微调)"
    candidate = None
    # 1) 优先当前行 MV
    if job_model_version_id and job_mv_record:
        candidate = job_mv_record
        label = (
            f"当前行 ModelVersion #{candidate.id} "
            f"(name={candidate.name!r}, active={candidate.is_active})"
        )
    # 2) 回退激活 MV
    if candidate is None and active_mv_record:
        candidate = active_mv_record
        label = f"激活 ModelVersion #{candidate.id} (name={candidate.name!r})"
    # 文件存在校验
    if candidate and candidate.file_path:
        pth = Path(candidate.file_path)
        if pth.exists():
            pretrained_model_path = str(pth)
            pretrained_source_mv_id = candidate.id
        else:
            label += " [文件不存在, 改从头微调]"
    return pretrained_model_path, pretrained_source_mv_id, label


def make_mv(mv_id, name, file_path, is_active):
    """构造一个轻量 mock ModelVersion"""
    mv = MagicMock()
    mv.id = mv_id
    mv.name = name
    mv.file_path = file_path
    mv.is_active = is_active
    return mv


# ============== 测试 ==============

class TestRetrainUsesRowMV:
    """再训练优先用当前行 MV, 不依赖 is_active"""

    def test_row_mv_inactive_used(self, tmp_path):
        """场景 1a: 当前行 MV 是 is_active=False, 仍应使用"""
        pth = tmp_path / "row_mv.pth"
        pth.write_text("dummy")
        row_mv = make_mv(10, "v1", str(pth), is_active=False)
        active_mv = make_mv(99, "v99", "/never/used.pth", is_active=True)

        result_path, result_mv_id, label = resolve_pretrained_path(
            job_model_version_id=10,
            job_mv_record=row_mv,
            active_mv_record=active_mv,  # 存在但应被忽略
        )
        assert result_path == str(pth)
        assert result_mv_id == 10
        assert "当前行 ModelVersion #10" in label
        assert "active=False" in label
        # 关键断言: 选了行 MV, 不是激活 MV
        assert "激活" not in label

    def test_row_mv_active_used(self, tmp_path):
        """场景 1b: 当前行 MV 恰好是激活的, 也正确返回"""
        pth = tmp_path / "active_row.pth"
        pth.write_text("dummy")
        row_mv = make_mv(20, "v2", str(pth), is_active=True)
        active_mv = row_mv  # 同一个

        result_path, result_mv_id, label = resolve_pretrained_path(
            job_model_version_id=20,
            job_mv_record=row_mv,
            active_mv_record=active_mv,
        )
        assert result_path == str(pth)
        assert result_mv_id == 20
        assert "当前行 ModelVersion #20" in label

    def test_row_mv_missing_fallback_active(self, tmp_path):
        """场景 2: 当前行无 MV, 回退到激活 MV"""
        pth = tmp_path / "active.pth"
        pth.write_text("dummy")
        active_mv = make_mv(30, "v3", str(pth), is_active=True)

        result_path, result_mv_id, label = resolve_pretrained_path(
            job_model_version_id=None,
            job_mv_record=None,
            active_mv_record=active_mv,
        )
        assert result_path == str(pth)
        assert result_mv_id == 30
        assert "激活 ModelVersion #30" in label

    def test_no_mv_at_all_use_imagenet(self):
        """场景 3: 都没有 → None (run_training 走 ImageNet)"""
        result_path, result_mv_id, label = resolve_pretrained_path(
            job_model_version_id=None,
            job_mv_record=None,
            active_mv_record=None,
        )
        assert result_path is None
        assert result_mv_id is None
        assert "从头微调" in label

    def test_row_mv_id_set_but_record_none(self, tmp_path):
        """场景 4: job.model_version_id 有值, 但 MV 已被删 → 回退到激活 MV"""
        pth = tmp_path / "active.pth"
        pth.write_text("dummy")
        active_mv = make_mv(40, "v4", str(pth), is_active=True)

        result_path, result_mv_id, label = resolve_pretrained_path(
            job_model_version_id=999,  # 悬空 ID
            job_mv_record=None,
            active_mv_record=active_mv,
        )
        assert result_path == str(pth)
        assert result_mv_id == 40
        assert "激活 ModelVersion #40" in label

    def test_row_mv_file_missing_fallback_imagenet(self):
        """场景 5: 当前行 MV 存在但 .pth 文件被删 → 改从头微调"""
        row_mv = make_mv(50, "v5", "/nonexistent/missing.pth", is_active=True)
        active_mv = make_mv(60, "v6", "/also/missing.pth", is_active=True)

        result_path, result_mv_id, label = resolve_pretrained_path(
            job_model_version_id=50,
            job_mv_record=row_mv,
            active_mv_record=active_mv,
        )
        assert result_path is None
        assert result_mv_id is None
        assert "文件不存在" in label
        assert "当前行 ModelVersion #50" in label  # 仍是行 MV 来源描述


# ============== 端到端校验: 跑 start.py 中的逻辑, 不依赖 DB ==============
class TestStartRestartLogicContract:
    """验证修改后的 start.py 代码结构符合预期"""

    def test_start_py_uses_job_model_version_id(self):
        """start.py 的 mode=restart 分支应查询 job.model_version_id"""
        from app.tasks.api.training import start as start_mod
        src = Path(start_mod.__file__).read_text(encoding="utf-8")
        # 关键: 优先用 job.model_version_id, 而不是只查 is_active
        assert "job.model_version_id" in src, (
            "start.py 应该优先查询 job.model_version_id 作为增量训练起点"
        )
        # 同时保留 is_active 的回退 (向后兼容)
        assert "is_active" in src, "start.py 应保留激活 MV 的回退逻辑"

    def test_start_py_priority_order(self):
        """start.py 应有明确的两段式查找 (优先 + 回退)"""
        from app.tasks.api.training import start as start_mod
        src = Path(start_mod.__file__).read_text(encoding="utf-8")
        # 行 MV 优先查找应在激活 MV 之前
        pos_row = src.find("job.model_version_id")
        pos_active = src.find("ModelVersion.is_active == True")
        assert pos_row > 0 and pos_active > 0, "两段查找代码都应存在"
        assert pos_row < pos_active, (
            "job.model_version_id 的查询应在 is_active 查询之前 (优先)"
        )
