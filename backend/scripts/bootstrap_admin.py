"""
Bootstrap Admin User (首次部署管理员初始化)
==========================================

**v3.6.0 增强**:
- 自动从 .env 的 ADMIN_USERNAME / ADMIN_PASSWORD / ADMIN_EMAIL 读取默认账号
- 系统无任何 admin 时自动放行, 跳过 "确认覆盖" 提示 (首次部署快路径)
- 新增 --check 子命令: 仅检查系统是否存在 admin, 不修改数据
  (供 verify_deployment.py 集成, 检测"系统无可用 admin"风险)
- 生产环境 (APP_ENV=production) 仅允许从环境变量 bootstrap,
  拒绝 CLI 明文密码, 避免 secret 进 shell history

**使用**:
```bash
# 方式 1: CLI 显式 (本地开发)
cd backend
python -m scripts.bootstrap_admin --username admin --password "YourStrong!Pass1" --email admin@example.com

# 方式 2: 从 .env 自动读取 (Docker 首启 / 一键部署)
#   .env 中:
#     ADMIN_USERNAME=admin
#     ADMIN_PASSWORD=YourStrong!Pass1
#     ADMIN_EMAIL=admin@example.com
cd backend
python -m scripts.bootstrap_admin

# 方式 3: 仅检查
python -m scripts.bootstrap_admin --check
```

**安全**:
- 密码强度: 至少 8 位 (推荐 12+ 含大小写/数字/特殊)
- APP_ENV=production: 拒绝从 CLI 读取 --password 明文, 只允许 .env 注入
- 已存在同名用户拒绝; 已存在其他 admin 时, 首次部署 (无任何 admin) 自动放行
- 不修改现有 admin 密码
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

# 允许在 backend/ 下运行: python -m scripts.bootstrap_admin
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


async def _has_any_admin(db) -> bool:
    """系统是否已有任意 admin 用户"""
    from sqlalchemy import select, func
    from app.admin.model.user import User

    result = await db.execute(
        select(func.count()).select_from(User).where(User.role == "admin")
    )
    return int(result.scalar() or 0) > 0


async def _user_exists(db, username: str) -> bool:
    """指定用户名是否已存在"""
    from sqlalchemy import select
    from app.admin.model.user import User

    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none() is not None


async def _create_admin(
    username: str,
    password: str,
    email: "str | None",
    *,
    confirm_existing: bool = True,
) -> int:
    """创建 admin 主逻辑.

    Args:
        username: 用户名
        password: 明文密码 (内部会 hash)
        email: 邮箱, 可空
        confirm_existing: 当系统已有其他 admin 时是否要求二次确认.
            - 首次部署 (无任何 admin): 自动放行
            - 已有 admin + CLI 显式调用: 默认要求 y/N 二次确认 (confirm_existing=True)
            - 已有 admin + 自动 bootstrap (来自 .env): 静默跳过, 不创建新 admin
    """
    from app.core.config import settings
    from app.database import AsyncSessionLocal
    from app.admin.model.user import User
    from app.middleware.security.security import get_password_hash

    # 密码强度
    if len(password) < 8:
        print(f"[FAIL] 密码至少 8 位, 当前 {len(password)} 位")
        return 1

    async with AsyncSessionLocal() as db:
        # 1) 同名用户已存在 → 拒绝
        if await _user_exists(db, username):
            print(f"[FAIL] 用户名 {username!r} 已存在")
            return 1

        # 2) 已有任意 admin → 决定是否继续
        if await _has_any_admin(db):
            if not confirm_existing:
                print(
                    "[SKIP] 系统已有 admin, 自动 bootstrap 模式下不再创建新 admin "
                    "(避免误覆盖). 如需新增 admin, 请登录后由 admin 通过 /api/users 创建."
                )
                return 0
            # CLI 模式: 二次确认
            ans = input("      系统已有 admin, 仍要创建新 admin? (yes/no): ").strip().lower()
            if ans != "yes":
                print("      已取消")
                return 0

        # 3) 创建
        user = User(
            username=username,
            password_hash=get_password_hash(password),
            email=email,
            role="admin",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        print(f"[OK] 已创建 admin 用户:")
        print(f"     username: {user.username}")
        print(f"     user_id : {user.id}")
        print(f"     role    : {user.role}")
        if email:
            print(f"     email   : {email}")
        print(f"\n     请立即登录并修改默认密码!")
        return 0


async def _check() -> int:
    """仅检查系统 admin 状态, 不修改数据.

    退出码:
        0 - 系统已有 >=1 个 admin
        1 - 系统无 admin (部署不完整风险)
    """
    from app.core.config import settings
    from app.database import AsyncSessionLocal
    from app.admin.model.user import User
    from sqlalchemy import select, func

    print(f"[check] APP_ENV = {settings.APP_ENV}")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(func.count()).select_from(User).where(User.role == "admin")
        )
        n_admin = int(result.scalar() or 0)
        result = await db.execute(
            select(func.count()).select_from(User)
        )
        n_total = int(result.scalar() or 0)

    print(f"[check] admin 用户数: {n_admin}")
    print(f"[check] 总用户数    : {n_total}")
    if n_admin == 0:
        print("[FAIL] 系统无任何 admin 用户, 请执行 bootstrap_admin 创建")
        return 1
    print("[OK] 系统已有 admin, 可正常登录")
    return 0


def main():
    p = argparse.ArgumentParser(
        prog="bootstrap_admin",
        description="创建首个 admin 用户 (dev: CLI 自由; prod: 仅 .env 自动)",
    )
    p.add_argument("--username", default=None, help="用户名 (3-50 字符). 留空则从 settings.ADMIN_USERNAME 读取")
    p.add_argument("--password", default=None, help="密码 (>=8 位). 留空则从 settings.ADMIN_PASSWORD 读取")
    p.add_argument("--email", default=None, help="邮箱 (可选). 留空则从 settings.ADMIN_EMAIL 读取")
    p.add_argument(
        "--check",
        action="store_true",
        help="仅检查系统是否存在 admin, 不创建 (供 verify_deployment.py 集成)",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="已有 admin 时跳过二次确认 (脚本/CI 场景)",
    )
    args = p.parse_args()

    # --check 短路
    if args.check:
        rc = asyncio.run(_check())
        sys.exit(rc)

    # 解析最终用户名/密码/邮箱: CLI 优先, 否则读 settings
    from app.core.config import settings

    username = args.username or settings.ADMIN_USERNAME
    password = args.password or settings.ADMIN_PASSWORD
    email = args.email or settings.ADMIN_EMAIL

    if not username or not password:
        print(
            "[FAIL] 未提供 username/password, 也未在 .env 设置 ADMIN_USERNAME/ADMIN_PASSWORD\n"
            "       用法: python -m scripts.bootstrap_admin --username admin --password 'xxx'"
        )
        sys.exit(1)

    # 生产环境: 拒绝 CLI 明文密码
    if settings.APP_ENV == "production" and args.password:
        print(
            "[FAIL] APP_ENV=production 禁止从 CLI 读取 --password 明文, "
            "请通过 .env 的 ADMIN_PASSWORD 注入 (由密钥管理平台管理)"
        )
        sys.exit(1)

    # 自动模式 (来自 .env): 已有 admin 时静默跳过, 不弹确认
    auto_mode = not (args.username and args.password)
    confirm_existing = (not auto_mode) and (not args.yes)

    rc = asyncio.run(
        _create_admin(username, password, email, confirm_existing=confirm_existing)
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
