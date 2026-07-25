"""
支持 `python -m app` 启动
========================
等价于在 backend 目录下执行 `python run.py`。
"""
from app.core.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
