"""
数据库迁移执行器 (ordered migration runner)
===========================================

**职责**:
1. 从 _registry 读取所有迁移声明, 按 order 升序执行
2. 每个迁移文件必须暴露 `async def run_migration() -> None` (无参入口)
3. 严格按顺序执行, 一旦失败立即停止, 防止数据库结构异常
4. 全部幂等, 重复执行安全

**调用方式**:
- 启动时自动执行: 在 app.main.lifespan() 调 `await run_all_migrations()`
- 手动触发: `python -m migrations.runner` 或 `python migrations/runner.py`
- 单条调试: `python -m migrations.runner --only 03` (只跑 03)
- 从某条开始: `python -m migrations.runner --from 05` (跑 05 及之后)

**约束**:
- 序号唯一, 不可重复
- 序号必须从 00 开始
- 序号可以不连续 (允许预留, e.g. 02, 03 之后跳 05, 留 04 给未来)
- 每个脚本顶层定义 MIGRATION_ID 常量 (字符串, 与 order 一致)
- run_migration() 不得依赖其他模块的"先于本脚本执行"假设, 除非在本脚本显式声明
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# 让本文件既可作为模块导入, 也可作为脚本直接运行
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from migrations._registry import Migration, get_migrations  # noqa: E402

logger = logging.getLogger("migrations.runner")


# ============================================================
#  迁移文件命名校验
# ============================================================
# 合法文件名: NN_name.py, NN 为两位数字 (00-99)
_MIGRATION_FILE_PATTERN = re.compile(r"^(\d{2})_[a-z0-9_]+\.py$")


@dataclass
class MigrationResult:
    """单条迁移执行结果"""
    order: str
    name: str
    success: bool
    duration_ms: float
    error: Optional[str] = None

    def format_log(self) -> str:
        status = "OK" if self.success else "FAIL"
        return (
            f"[{status}] [{self.order}] {self.name}  "
            f"({self.duration_ms:.1f}ms)"
            + (f"  err={self.error}" if self.error else "")
        )


def list_migration_files() -> List[Path]:
    """扫描磁盘上所有合法命名的迁移文件 (按文件名升序)

    校验项:
    - 文件名必须匹配 `NN_name.py` 模式
    - NN 必须是两位数字 (00-99)
    """
    migrations_dir = Path(__file__).resolve().parent
    files: list[Path] = []
    for f in migrations_dir.iterdir():
        if f.is_file() and f.suffix == ".py":
            if _MIGRATION_FILE_PATTERN.match(f.name):
                files.append(f)
    return sorted(files, key=lambda p: p.name)


def _load_migration_module(migration: Migration):
    """导入迁移模块, 校验必备接口"""
    try:
        module = importlib.import_module(migration.module)
    except ImportError as e:
        raise RuntimeError(
            f"[migrations.runner] 无法导入迁移模块 {migration.module}: {e}"
        ) from e

    # 必备: run_migration() 异步函数
    if not hasattr(module, "run_migration"):
        raise RuntimeError(
            f"[migrations.runner] 迁移 {migration.order}/{migration.name} "
            f"({migration.module}) 未定义 run_migration() 入口"
        )
    run_fn = getattr(module, "run_migration")
    if not asyncio.iscoroutinefunction(run_fn):
        raise RuntimeError(
            f"[migrations.runner] {migration.module}.run_migration "
            f"必须是 async 函数"
        )
    return module


async def execute_migration(migration: Migration) -> MigrationResult:
    """执行单条迁移, 捕获异常并返回结果"""
    logger.info("[migrations] 执行 [%s] %s ...", migration.order, migration.name)
    start = time.perf_counter()
    try:
        module = _load_migration_module(migration)
        await module.run_migration()
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "[migrations] 完成 [%s] %s (%.1fms)",
            migration.order, migration.name, duration_ms,
        )
        return MigrationResult(
            order=migration.order,
            name=migration.name,
            success=True,
            duration_ms=duration_ms,
        )
    except Exception as e:  # noqa: BLE001
        duration_ms = (time.perf_counter() - start) * 1000
        err_msg = f"{type(e).__name__}: {e}"
        logger.exception(
            "[migrations] 失败 [%s] %s (%.1fms): %s",
            migration.order, migration.name, duration_ms, err_msg,
        )
        return MigrationResult(
            order=migration.order,
            name=migration.name,
            success=False,
            duration_ms=duration_ms,
            error=err_msg,
        )


def _filter_migrations(
    migrations: List[Migration],
    only: Optional[str],
    start_from: Optional[str],
) -> List[Migration]:
    """按命令行参数过滤迁移列表"""
    sorted_migs = sorted(migrations, key=lambda m: m.order)
    if only:
        filtered = [m for m in sorted_migs if m.order == only]
        if not filtered:
            raise ValueError(
                f"[migrations.runner] --only {only} 找不到对应迁移, "
                f"可用: {[m.order for m in sorted_migs]}"
            )
        return filtered
    if start_from:
        idx = next(
            (i for i, m in enumerate(sorted_migs) if m.order == start_from),
            None,
        )
        if idx is None:
            raise ValueError(
                f"[migrations.runner] --from {start_from} 找不到对应迁移, "
                f"可用: {[m.order for m in sorted_migs]}"
            )
        return sorted_migs[idx:]
    return sorted_migs


def validate_registry_consistency() -> list[str]:
    """校验 _registry 与磁盘文件的一致性

    返回: 警告信息列表 (空 = 完全一致)
    """
    warnings: list[str] = []
    files = list_migration_files()
    file_orders = {
        _MIGRATION_FILE_PATTERN.match(f.name).group(1)
        for f in files
    }
    registry_orders = {m.order for m in get_migrations()}

    # 1. 磁盘上存在但未在 registry 声明的文件
    orphan = file_orders - registry_orders
    for order in sorted(orphan):
        warnings.append(
            f"磁盘存在迁移文件 (order={order}), 但 _registry.MIGRATIONS 未声明"
        )

    # 2. registry 声明了但磁盘上找不到文件
    missing = registry_orders - file_orders
    for order in sorted(missing):
        warnings.append(
            f"_registry 声明了 order={order}, 但磁盘上找不到对应文件"
        )

    # 3. registry 序号是否严格升序且无重复
    orders = [m.order for m in get_migrations()]
    if len(set(orders)) != len(orders):
        warnings.append(f"_registry MIGRATIONS 存在重复 order: {orders}")

    return warnings


async def run_all_migrations(
    only: Optional[str] = None,
    start_from: Optional[str] = None,
    stop_on_error: bool = True,
) -> List[MigrationResult]:
    """执行所有迁移 (按 order 升序)

    Args:
        only: 只执行指定 order (如 "03"), 其他全部跳过
        start_from: 从指定 order 开始执行 (含), 之前的跳过
        stop_on_error: 失败时是否停止后续 (默认 True, 安全策略)

    Returns:
        每条迁移的执行结果
    """
    migrations = get_migrations()
    migrations = _filter_migrations(migrations, only, start_from)

    if not migrations:
        logger.warning("[migrations] 没有可执行的迁移 (filter=%s, from=%s)", only, start_from)
        return []

    logger.info(
        "[migrations] 计划执行 %d 条迁移: %s",
        len(migrations),
        ", ".join(f"{m.order}={m.name}" for m in migrations),
    )

    results: list[MigrationResult] = []
    for migration in migrations:
        result = await execute_migration(migration)
        results.append(result)
        if not result.success and stop_on_error:
            logger.error(
                "[migrations] [%s] 失败, 终止后续执行 (避免数据库结构异常)",
                migration.order,
            )
            break

    return results


def _print_summary(results: list[MigrationResult]) -> None:
    """打印执行汇总"""
    if not results:
        print("\n[migrations] 没有执行任何迁移")
        return
    print("\n========== 迁移执行汇总 ==========")
    for r in results:
        print(r.format_log())
    success_n = sum(1 for r in results if r.success)
    fail_n = sum(1 for r in results if not r.success)
    total_ms = sum(r.duration_ms for r in results)
    print(f"\n成功 {success_n} / 失败 {fail_n} / 总耗时 {total_ms:.1f}ms")
    if fail_n > 0:
        print("\n警告: 部分迁移失败, 数据库结构可能不完整, 请检查后重试")
    print("=" * 40)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="数据库迁移执行器 (按 order 升序执行所有迁移)",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="只执行指定 order (e.g. --only 03)",
    )
    parser.add_argument(
        "--from",
        dest="start_from",
        type=str,
        default=None,
        help="从指定 order 开始执行 (含), e.g. --from 05",
    )
    parser.add_argument(
        "--no-stop",
        action="store_true",
        help="失败不停止, 继续执行后续 (默认失败会立即停止)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出所有迁移, 不执行",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="校验 _registry 与磁盘文件的一致性, 不执行",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="输出 DEBUG 级别日志",
    )
    return parser.parse_args()


async def async_main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    # --list: 只列清单
    if args.list:
        print("已注册迁移 (按 order 升序):")
        for m in get_migrations():
            print(f"  [{m.order}] {m.name}")
            print(f"       {m.description}")
        return 0

    # --check: 校验一致性
    if args.check:
        warnings = validate_registry_consistency()
        if not warnings:
            print("[OK] _registry 与磁盘文件完全一致, 共 %d 条迁移" % len(get_migrations()))
            return 0
        print("[FAIL] 发现 %d 个一致性问题:" % len(warnings))
        for w in warnings:
            print(f"  - {w}")
        return 1

    # 执行迁移
    results = await run_all_migrations(
        only=args.only,
        start_from=args.start_from,
        stop_on_error=not args.no_stop,
    )
    _print_summary(results)
    has_fail = any(not r.success for r in results)
    return 0 if not has_fail else 1


def main() -> int:
    """同步入口 (供 CLI 与 lifespan 钩子调用)"""
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "Migration",
    "MigrationResult",
    "execute_migration",
    "run_all_migrations",
    "validate_registry_consistency",
    "main",
]
