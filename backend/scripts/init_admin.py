"""
部署后初始化脚本: 创建首个 admin 用户 (绕过 bootstrap_admin 的 production 限制)

用途: 当数据库 user 表为空时, 由运维一次性执行创建初始管理员
用法: docker exec annotation_api python -m scripts.init_admin
"""
import asyncio
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


async def _init_admin() -> int:
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.admin.model.user import User
    from app.middleware.security.security import get_password_hash

    # 导入 app.main 触发所有模型注册, 确保 SQLAlchemy mapper 能解析 User 的 relationship
    import app.main  # noqa: F401

    # 默认凭据 (生产环境部署后请立即修改)
    USERNAME = "admin"
    PASSWORD = "admin123"
    EMAIL = "admin@example.com"

    async with AsyncSessionLocal() as db:
        # 1) 检查是否已有任何用户
        result = await db.execute(select(User).limit(1))
        if result.scalar_one_or_none():
            print("[SKIP] 数据库已存在用户, 跳过初始化")
            return 0

        # 2) 检查用户名是否被占用
        result = await db.execute(select(User).where(User.username == USERNAME))
        if result.scalar_one_or_none():
            print(f"[FAIL] 用户名 {USERNAME!r} 已存在")
            return 1

        # 3) 创建 admin
        user = User(
            username=USERNAME,
            password_hash=get_password_hash(PASSWORD),
            email=EMAIL,
            role="admin",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        print(f"[OK] 初始 admin 用户已创建:")
        print(f"     username: {user.username}")
        print(f"     user_id : {user.id}")
        print(f"     role    : {user.role}")
        print(f"     email   : {user.email}")
        print(f"\n     请立即登录并修改密码!")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_init_admin()))
