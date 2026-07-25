"""
Backend 一键启动入口
====================
最常用的启动方式：

    cd backend
    python run.py                  # 默认 127.0.0.1:5000
    python run.py --host 0.0.0.0   # 允许局域网访问
    python run.py --reload         # 开发模式：代码改动自动重载
    python run.py --port 8000
    python run.py --workers 4      # 生产模式多 worker
    python run.py --check          # 只做环境检查，不启动

启动后访问：
    API       : http://127.0.0.1:5000/
    Swagger   : http://127.0.0.1:5000/docs
    ReDoc     : http://127.0.0.1:5000/redoc
    Health    : http://127.0.0.1:5000/api/health
"""
from app.core.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
