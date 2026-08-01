from app.core.config import settings
print(f"env file: {settings.model_config['env_file']}")
print(f"APP_ENV={settings.APP_ENV}")
sk = settings.SECRET_KEY or ''
print(f"SECRET_KEY prefix: {sk[:8]}")
print(f"MYSQL_HOST={settings.MYSQL_HOST}")
mp = settings.MYSQL_PASSWORD or ''
print(f"MYSQL_PASSWORD prefix: {mp[:6]}")
print(f"REDIS_HOST={settings.REDIS_HOST}")
print(f"MINIO_ENDPOINT={settings.MINIO_ENDPOINT}")
print(f"APP_PORT={settings.APP_PORT}")
print(f"CORS_ORIGINS={settings.CORS_ORIGINS}")
