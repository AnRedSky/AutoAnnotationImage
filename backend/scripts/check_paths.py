from pathlib import Path
import os
os.environ.setdefault("APP_ENV", "test")

# 模拟 config.py 的路径计算
config_path = Path(r"D:\works\WorkBuddy\Myhome\ThesisDesignImplementation\thesis-image-annotation\backend\app\core\config.py")
project_root_env = config_path.parent.parent.parent.parent / ".env"
backend_env = config_path.parent.parent.parent / ".env"
print(f"project root .env: {project_root_env}")
print(f"  exists: {project_root_env.exists()}")
if project_root_env.exists():
    content = project_root_env.read_text(encoding="utf-8")
    for line in content.splitlines():
        if line.startswith("SECRET_KEY=") or line.startswith("APP_ENV="):
            print(f"  {line}")
print()
print(f"backend .env: {backend_env}")
print(f"  exists: {backend_env.exists()}")
if backend_env.exists():
    content = backend_env.read_text(encoding="utf-8")
    for line in content.splitlines():
        if line.startswith("SECRET_KEY=") or line.startswith("APP_ENV="):
            print(f"  {line}")
