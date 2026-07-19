"""
v2.0.0 迁移脚本 (thin wrapper, 业务逻辑在 app.core.db_migration)
================================================================

应用启动时 (init_db) 会自动调用 ensure_v2_0_0_schema, 无需手动跑本脚本.
保留本脚本作为手动触发 / CI 验证 / 排错用途.

用法:
    cd backend
    python scripts/migrate_v2_0_0.py

幂等: 第二次起全部 skip.
"""
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.db_migration import MIGRATIONS, ensure_v2_0_0_schema  # noqa: E402


def get_url() -> str:
    """支持 -d / --database-url 覆盖 (调试用)"""
    for i, arg in enumerate(sys.argv):
        if arg in ("-d", "--database-url") and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return settings.EFFECTIVE_DATABASE_URL


async def main() -> int:
    url = get_url()
    print(f"[migrate_v2_0_0] DATABASE_URL = {url}")
    print(f"[migrate_v2_0_0] 待补 {len(MIGRATIONS)} 列")
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            result = await ensure_v2_0_0_schema(conn, verbose=True)
        print(
            f"\n[migrate_v2_0_0] 完成: 新增 {len(result['added'])} 列, "
            f"跳过 {len(result['skipped'])}, 失败 {len(result['errors'])}"
        )
        return 0 if not result["errors"] else 1
    except Exception as e:  # noqa: BLE001
        print(f"\n[migrate_v2_0_0] 失败: {e!r}")
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
