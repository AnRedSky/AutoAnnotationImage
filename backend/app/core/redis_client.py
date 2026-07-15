"""
Redis Client Singleton
======================
统一的 Redis 客户端，供 Celery broker / 缓存 / 训练历史读取共用

关键：必须从 .env 实时读取端口，而不是 os.getenv（避免 import 顺序问题）。
app.config.settings 用了 pydantic-settings 自动从 .env 加载，端口统一从那里来。
"""
from app.config import settings
import redis

REDIS_HOST = settings.REDIS_HOST
REDIS_PORT = settings.REDIS_PORT
REDIS_DB = settings.REDIS_DB
REDIS_PASSWORD = settings.REDIS_PASSWORD or None

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True,
    socket_connect_timeout=5,   # 2s 太短，连接握手偶发超时
    socket_timeout=5,           # 2s 太短，读写偶发超时
    socket_keepalive=True,
    health_check_interval=30,   # 30s 一次 PING，断开自动重连
)


def get_redis() -> redis.Redis:
    """依赖注入用：获取 redis 客户端"""
    return redis_client
