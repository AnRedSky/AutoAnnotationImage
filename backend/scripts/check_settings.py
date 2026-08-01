import os
import sys
# 强制使用项目根 .env
os.chdir(r"D:\works\WorkBuddy\Myhome\ThesisDesignImplementation\thesis-image-annotation")
sys.path.insert(0, r"D:\works\WorkBuddy\Myhome\ThesisDesignImplementation\thesis-image-annotation\backend")

# 先清除可能影响的环境变量
for k in ["SECRET_KEY", "JWT_SECRET", "APP_ENV", "MYSQL_PASSWORD"]:
    os.environ.pop(k, None)

try:
    from app.core.config import settings
    print(f"✓ 配置加载成功")
    print(f"  env_file: {settings.model_config['env_file']}")
    print(f"  APP_ENV={settings.APP_ENV}")
    print(f"  SECRET_KEY 前 8: {(settings.SECRET_KEY or '')[:8]}")
    print(f"  MYSQL_HOST={settings.MYSQL_HOST}:{settings.MYSQL_PORT}")
    print(f"  REDIS_HOST={settings.REDIS_HOST}:{settings.REDIS_PORT}")
    print(f"  MINIO_ENDPOINT={settings.MINIO_ENDPOINT}")
    print(f"  APP_PORT={settings.APP_PORT}")
    print(f"  CORS_ORIGINS={settings.CORS_ORIGINS}")
except Exception as e:
    print(f"✗ 失败: {e}")
    import traceback
    traceback.print_exc()
