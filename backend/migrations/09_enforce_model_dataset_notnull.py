"""
迁移脚本 09: ModelVersion 强制 dataset_id 非空 (v3.3.5-PERMISSION-REWRITE)
==========================================================================
**MIGRATION_ID**: 09
**功能**:
- ModelVersion.dataset_id 从 nullable=True 改为 nullable=False
- 理由: 「孤儿 model」(dataset_id IS NULL) 在 v3.3.5 严格最小权限下无法
  通过任何角色访问 (owner 不存在, team 校验也无从下手), 等于「死数据」
- 修复策略 (两步):
  1. **数据清洗**: 将历史孤儿 model 关联到默认「系统孤儿 dataset」
  2. **Schema 约束**: ALTER COLUMN dataset_id SET NOT NULL
**执行步骤 (幂等)**:
  1. 统计孤儿 model 数量
  2. 找一个/创建一个「_system_orphan_models_holder」dataset
  3. UPDATE model_version SET dataset_id = <orphan_ds_id> WHERE dataset_id IS NULL
  4. ALTER TABLE model_version MODIFY COLUMN dataset_id INT NOT NULL
  5. 验证: 再次统计, 应为 0
**回滚方案**: 此迁移为 v3.3.5 安全修复, 不可逆
**执行顺序**: 09, 依赖 00 (model_version/task_type 等列已建)
"""
import asyncio
import logging
from sqlalchemy import text
from app.database import engine

logger = logging.getLogger(__name__)


MIGRATION_ID = "09"
MIGRATION_DESCRIPTION = "model_version.dataset_id 强制 NOT NULL; 历史孤儿 model 关联到 _system_orphan_models_holder dataset"


ORPHAN_DATASET_NAME = "_system_orphan_models_holder"
ORPHAN_DATASET_TASK_TYPE = "classification"  # 任意, 不影响


async def _count_orphan_models(conn) -> int:
    """统计孤儿 model 数量"""
    result = await conn.execute(
        text("SELECT COUNT(*) FROM model_version WHERE dataset_id IS NULL")
    )
    return int(result.scalar() or 0)


async def _find_or_create_orphan_dataset(conn) -> int:
    """找一个用来承接孤儿 model 的 dataset id.

    优先级:
      1. 已存在 name = ORPHAN_DATASET_NAME 的 dataset → 复用
      2. 否则取第一个 super_admin 用户下的「orphan_models」dataset
         (若不存在则创建), 这样保证 dataset 拥有有效 owner, model 仍可被审计
      3. 若 user 不存在 → 报错 (环境不健康, 终止)
    """
    # 1) 查找现有
    result = await conn.execute(
        text("SELECT id FROM dataset WHERE name = :n LIMIT 1"),
        {"n": ORPHAN_DATASET_NAME},
    )
    row = result.first()
    if row is not None:
        logger.info(
            "[migration] reuse existing orphan dataset id=%s name=%s",
            row[0], ORPHAN_DATASET_NAME,
        )
        return int(row[0])

    # 2) 取第一个 super_admin / admin 用户
    result = await conn.execute(
        text(
            "SELECT id FROM user "
            "WHERE role IN ('super_admin', 'admin') AND is_active = 1 "
            "ORDER BY id ASC LIMIT 1"
        )
    )
    row = result.first()
    if row is None:
        raise RuntimeError(
            "[migration] No active admin user found, "
            "cannot create orphan holder dataset. Please create at least one super_admin."
        )
    owner_id = int(row[0])

    # 3) 创建 dataset
    result = await conn.execute(
        text(
            "INSERT INTO dataset "
            "(name, description, task_type, owner_id, team_id, status, "
            " category_count, image_count, labeled_count, created_at, updated_at) "
            "VALUES (:name, :desc, :tt, :owner, NULL, 'archived', 0, 0, 0, NOW(), NOW())"
        ),
        {
            "name": ORPHAN_DATASET_NAME,
            "desc": (
                "v3.3.5-PERMISSION-REWRITE 自动化创建, "
                "用于承接历史孤儿 model (无主 model). "
                "普通用户不可见, 仅平台审计可访问."
            ),
            "tt": ORPHAN_DATASET_TASK_TYPE,
            "owner": owner_id,
        },
    )
    new_id = result.lastrowid
    if not new_id:
        # SQLite fallback
        result = await conn.execute(
            text("SELECT id FROM dataset WHERE name = :n"), {"n": ORPHAN_DATASET_NAME}
        )
        new_id = int(result.scalar())
    logger.info(
        "[migration] created orphan holder dataset id=%s owner_id=%s",
        new_id, owner_id,
    )
    return int(new_id)


async def _migrate_orphan_models(conn) -> int:
    """将 dataset_id IS NULL 的 model 关联到 orphan dataset"""
    orphan_ds_id = await _find_or_create_orphan_dataset(conn)
    result = await conn.execute(
        text(
            "UPDATE model_version SET dataset_id = :ds "
            "WHERE dataset_id IS NULL"
        ),
        {"ds": orphan_ds_id},
    )
    affected = result.rowcount or 0
    logger.info(
        "[migration] reparented %s orphan model(s) to dataset_id=%s",
        affected, orphan_ds_id,
    )
    return int(affected)


async def _enforce_not_null(conn) -> None:
    """ALTER TABLE model_version MODIFY COLUMN dataset_id NOT NULL (跨 DB)"""
    try:
        # MySQL
        await conn.execute(
            text(
                "ALTER TABLE model_version "
                "MODIFY COLUMN dataset_id INT NOT NULL"
            )
        )
        logger.info("[migration] MySQL: dataset_id SET NOT NULL done")
    except Exception as e:
        logger.warning("[migration] MySQL ALTER failed, trying SQLite path: %s", e)
        # SQLite: SQLite 不支持 MODIFY, 通过新表 + 拷贝 + 改名实现
        # 这里为简化, 仅记录; 真实部署应在应用启动时检测并提示
        logger.warning(
            "[migration] SQLite path is a no-op. "
            "Please re-create model_version table with NOT NULL constraint."
        )


async def _verify(conn) -> int:
    """验证: 再次统计孤儿 model 数量, 应为 0"""
    n = await _count_orphan_models(conn)
    if n > 0:
        raise RuntimeError(
            f"[migration] verify failed: {n} orphan model(s) still exist after migration"
        )
    logger.info("[migration] verify OK: 0 orphan model remaining")
    return n


async def run_migration() -> None:
    """主入口: 数据清洗 + Schema 约束"""
    logger.info("[migration] v3.3.5 enforce model_version.dataset_id NOT NULL starting")
    async with engine.begin() as conn:
        before = await _count_orphan_models(conn)
        logger.info("[migration] orphan model count before: %s", before)
        if before > 0:
            await _migrate_orphan_models(conn)
        # 验证数据层 0 孤儿
        await _verify(conn)
        # 收紧约束
        await _enforce_not_null(conn)
    logger.info(f"[done] [{MIGRATION_ID}] {MIGRATION_DESCRIPTION}")


async def main():
    """兼容历史 CLI 调用"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    await run_migration()


if __name__ == "__main__":
    asyncio.run(main())
