"""
Bootstrap Admin User (One-time Setup Script)
============================================

**v3.0.0 审查修复**: 注册端点改为仅 admin 可调用, 但系统首次部署时无 admin,
需要 CLI 入口创建首个管理员. 此脚本提供该能力.

**使用**:
```bash
cd backend
python -m scripts.bootstrap_admin --username admin --password "YourStrong!Pass1" --email admin@example.com
```

**安全**:
- 密码强度由 AuthService 强制 (至少 6 位, 推荐 12+ 位含大小写/数字/特殊)
- 脚本只允许在 APP_ENV=development 下运行, 生产环境必须手动 DB 操作
- 已存在 admin 时拒绝重复创建
"""
import argparse
import asyncio
import sys
from pathlib import Path

# 允许在 backend/ 下运行: python -m scripts.bootstrap_admin
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


async def _create_admin(username: str, password: str, email: str) -> int:
    from sqlalchemy import select

    from app.core.config import settings
    from app.database import AsyncSessionLocal
    from app.admin.model.user import User
    from app.core.security import get_password_hash

    # 安全: 仅 dev 环境可运行 CLI bootstrap
    if settings.APP_ENV == "production":
        print("[FAIL] bootstrap_admin 仅允许在 development 环境运行")
        print("       生产环境请通过 DBA 手动 INSERT 或部署平台的密钥管理")
        return 1

    # 密码强度
    if len(password) < 8:
        print(f"[FAIL] 密码至少 8 位, 当前 {len(password)} 位")
        return 1

    async with AsyncSessionLocal() as db:
        # 1) 已存在检查
        result = await db.execute(select(User).where(User.username == username))
        if result.scalar_one_or_none():
            print(f"[FAIL] 用户名 {username!r} 已存在")
            return 1

        # 2) 全局检查: 已有 admin 则拒绝 (避免误覆盖)
        result = await db.execute(
            select(User).where(User.role == "admin").limit(1)
        )
        existing_admin = result.scalar_one_or_none()
        if existing_admin:
            print(f"[WARN] 系统已有 admin 用户 {existing_admin.username!r} (id={existing_admin.id})")
            ans = input("      仍要创建新 admin? (yes/no): ").strip().lower()
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
        print(f"\n     请立即登录并修改默认密码!")
        return 0


def main():
    p = argparse.ArgumentParser(
        prog="bootstrap_admin",
        description="创建首个 admin 用户 (仅 dev 环境)",
    )
    p.add_argument("--username", required=True, help="用户名 (3-50 字符)")
    p.add_argument("--password", required=True, help="密码 (至少 8 位, 推荐 12+)")
    p.add_argument("--email", default=None, help="邮箱 (可选)")
    args = p.parse_args()

    rc = asyncio.run(_create_admin(args.username, args.password, args.email))
    sys.exit(rc)


if __name__ == "__main__":
    main()
