"""Reset admin password using correct column name."""
import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.user import User
from app.core.security import hash_password

async def main():
    async with AsyncSessionLocal() as db:
        u = (await db.execute(select(User).where(User.username == "admin"))).scalar_one_or_none()
        if not u:
            u = User(username="admin", email="admin@test.com",
                     password_hash=hash_password("admin123"),
                     is_active=True, role="admin")
            db.add(u)
            await db.commit()
            print("created admin user")
        else:
            u.password_hash = hash_password("admin123")
            u.is_active = True
            await db.commit()
            print("reset admin password")
        print("admin id =", u.id)

asyncio.run(main())
