"""临时脚本：验证新后端 API 编译通过"""
import os
# 切到 MySQL 配置（避免 aiosqlite 缺失） + 不实际连接
os.environ["APP_DEBUG"] = "False"

# import all api modules - mock the engine to avoid MySQL connect
import unittest.mock as mock
with mock.patch("app.database.create_async_engine") as mock_engine, \
     mock.patch("app.database.async_sessionmaker") as mock_sm:
    mock_engine.return_value = mock.MagicMock()
    mock_sm.return_value = mock.MagicMock()
    from app.api import image, annotation, training, files, dataset, model, stats
    from app.main import app

# 列出所有路由
print("=" * 60)
print("All routes:")
print("=" * 60)
for r in app.routes:
    if hasattr(r, 'methods') and hasattr(r, 'path'):
        methods = ",".join(sorted(m for m in r.methods if m not in ('HEAD', 'OPTIONS')))
        if '/api' in r.path:
            print(f"  {methods:8s} {r.path}")

print("\n[OK] import + route enumeration succeeded")
