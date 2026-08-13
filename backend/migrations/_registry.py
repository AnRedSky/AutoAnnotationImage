"""
数据库迁移注册表 (ordered migration registry)
===========================================

**目的**:
所有迁移脚本按两位数字序号 (00, 01, 02 ...) 命名, 本模块声明它们的标准执行顺序.
runner.py 在部署/启动时按本表顺序升序执行, 保证数据库变更的确定性与可追溯性.

**编号规则** (硬性约束, 见 README.md):
- 文件名格式: `NN_xxx.py`, NN 为两位数字 (00-99), 升序连续 (允许预留空号)
- 序号必须反映执行顺序, 早执行的脚本序号小
- 后一个脚本不得依赖前一个脚本未生成的列/表/索引
- 新增脚本必须:
  1. 取当前最大序号 + 1
  2. 追加到本表 MIGRATIONS 列表末尾
  3. 暴露 `run_migration()` 异步函数 (runner 调用入口)

**与 MIGRATIONS 文件系统的关系**:
- 磁盘上的 Nx_xxx.py 文件 → 通过文件名序号确定顺序
- 本表 MIGRATIONS 是**冗余保险**: 当文件命名不规范时, 仍按本表执行
- 实际执行顺序 = 序号 (本表的 ORDER 字段), runner 升序遍历

**新增迁移的标准流程**:
1. 在 backend/migrations/ 下创建 `NN_descriptive_name.py`
2. 在文件顶部加 MIGRATION_ID 常量, 暴露 `async def run_migration()`
3. 在本表 MIGRATIONS 追加一行: `Migration("NN", "short_name", "description", "module.path")`
4. 更新 README 的迁移清单表格
5. 在 tests/ 加一个 smoke test, 验证 run_migration() 幂等
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Migration:
    """单条迁移声明

    字段:
        order: 两位数字字符串, 升序执行 ("00" < "01" < "10" < "11")
        name: 简短名称, 用于日志 (建议和文件名后缀一致)
        description: 一句话功能描述, 用于 README 表格
        module: 导入路径 (e.g. "migrations.10_add_training_t7_indexes")
    """
    order: str
    name: str
    description: str
    module: str


# ============================================================
#  迁移清单 (按 order 升序, 新增迁移请追加到末尾)
# ============================================================
MIGRATIONS: List[Migration] = [
    Migration(
        order="00",
        name="add_detection_segmentation",
        description="v2.0.0: 新建 bbox_annotation/segmentation_mask 表, image/model_version/training_jobs 加 task_type, model_version 加 5 个任务指标 (map_50/map_50_95/miou/pixel_accuracy/dice_score)",
        module="migrations.00_add_detection_segmentation",
    ),
    Migration(
        order="01",
        name="extend_annotation_log_enum",
        description="v2.5.15 P0-2: annotation_log 加 payload JSON, action ENUM 扩展 (auto_annotate_pretrained/auto_annotate_finetuned)",
        module="migrations.01_extend_annotation_log_enum",
    ),
    Migration(
        order="02",
        name="add_training_log",
        description="training_jobs 加 log JSON 字段, 存训练日志行",
        module="migrations.02_add_training_log",
    ),
    Migration(
        order="03",
        name="add_training_created_at",
        description="training_jobs 加 created_at DATETIME 字段, 历史行用 started_at/finished_at 回填, 加索引",
        module="migrations.03_add_training_created_at",
    ),
    Migration(
        order="04",
        name="add_training_device_info",
        description="training_jobs 加训练资源字段 (device_type/device_name/device_info/gpu_peak_memory_mb)",
        module="migrations.04_add_training_device_info",
    ),
    Migration(
        order="05",
        name="add_training_pretrain_mode",
        description="training_jobs 加 pretrain_mode/pretrain_source_mv_id 字段, 加 pretrain_mode 索引 (依赖 add_training_device_info 之后, 因为同表加列需分步)",
        module="migrations.05_add_training_pretrain_mode",
    ),
    Migration(
        order="06",
        name="backfill_pretrain_mode_finetune",
        description="回填历史 training_jobs.pretrain_mode = 'from_scratch' (NULL → from_scratch, 强依赖 05 的字段已建)",
        module="migrations.06_backfill_pretrain_mode_finetune",
    ),
    Migration(
        order="07",
        name="add_team_enhance",
        description="v3.3.1: team.tenant_id/archived_at, team_member.invited_by_id, annotation_log.team_id; 加 3 个新索引; 回填 annotation_log.team_id",
        module="migrations.07_add_team_enhance",
    ),
    Migration(
        order="08",
        name="add_team_l5_indexes",
        description="v3.3.1 L5: 团队管理复合索引 (ix_audit_log_team_created/event_created/user_created/resource)",
        module="migrations.08_add_team_l5_indexes",
    ),
    Migration(
        order="09",
        name="enforce_model_dataset_notnull",
        description="v3.3.5: model_version.dataset_id 强制 NOT NULL; 历史孤儿 model 关联到 _system_orphan_models_holder dataset",
        module="migrations.09_enforce_model_dataset_notnull",
    ),
    Migration(
        order="10",
        name="add_training_t7_indexes",
        description="v3.5.0 Phase T7: training_jobs 索引优化 (base_model/model_name/user_type/dataset)",
        module="migrations.10_add_training_t7_indexes",
    ),
]


def get_migrations() -> List[Migration]:
    """按 order 升序返回迁移列表 (返回副本, 防止外部篡改)"""
    return sorted(MIGRATIONS, key=lambda m: m.order)


def get_max_order() -> int:
    """返回当前最大序号 (int), 新增迁移时 +1"""
    return max(int(m.order) for m in MIGRATIONS)


__all__ = ["Migration", "MIGRATIONS", "get_migrations", "get_max_order"]
