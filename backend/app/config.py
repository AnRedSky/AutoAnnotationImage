"""
Application Configuration
=========================
All configs from environment variables, with sensible defaults for dev.
兼容 .env (SECRET_KEY/MYSQL_DATABASE) 与 直传 (DATABASE_URL) 两种方式。
"""
import os
from pathlib import Path
from typing import List, Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置（pydantic-settings 2.x）"""

    # ===== App =====
    APP_NAME: str = "Image Annotation System"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # ===== Database =====
    # 优先使用 DATABASE_URL（测试用），否则根据 MYSQL_* 拼装
    DATABASE_URL: Optional[str] = None

    MYSQL_HOST: str = os.getenv("MYSQL_HOST", "localhost")
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER: str = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "root123")
    MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE", "image_annotation")
    MYSQL_DB: Optional[str] = None  # 兼容旧名
    MYSQL_ROOT_PASSWORD: str = os.getenv("MYSQL_ROOT_PASSWORD", "root")

    # ===== Redis =====
    REDIS_HOST: str = os.getenv("REDIS_HOST", "127.0.0.1")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    REDIS_PASSWORD: Optional[str] = os.getenv("REDIS_PASSWORD") or None

    CELERY_BROKER_URL: Optional[str] = os.getenv("CELERY_BROKER_URL")
    CELERY_RESULT_BACKEND: Optional[str] = os.getenv("CELERY_RESULT_BACKEND")

    # ===== JWT =====
    # 兼容 .env 的 SECRET_KEY 与旧名 JWT_SECRET
    SECRET_KEY: Optional[str] = os.getenv("SECRET_KEY")
    JWT_SECRET: Optional[str] = os.getenv("JWT_SECRET")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
    JWT_EXPIRE_MINUTES: int = 60 * 24  # 兼容旧名

    # ===== MinIO / Storage =====
    MINIO_ENDPOINT: str = os.getenv("MINIO_ENDPOINT", "127.0.0.1:9000")
    MINIO_ACCESS_KEY: str = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    MINIO_SECRET_KEY: str = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    MINIO_BUCKET: str = os.getenv("MINIO_BUCKET", "image-annotation")
    MINIO_SECURE: bool = os.getenv("MINIO_SECURE", "false").lower() == "true"

    # ===== File Storage =====
    UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR", "./uploads")).resolve()
    MODEL_DIR: Path = Path(os.getenv("MODEL_DIR", "./models")).resolve()
    MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "20"))

    # ===== ML =====
    DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", "efficientnet_b0")
    DEFAULT_BASE_MODEL: Optional[str] = None  # 兼容旧名
    DEFAULT_CONFIDENCE_THRESHOLD: float = float(os.getenv("DEFAULT_CONFIDENCE_THRESHOLD", "0.6"))
    INFERENCE_DEVICE: str = os.getenv("INFERENCE_DEVICE", "cpu")  # cpu | cuda

    # ===== HuggingFace 镜像（国内网络环境必须配）=====
    #  国内访问 huggingface.co 经常超时（WinError 10060），必须走镜像
    #  设置 HF_ENDPOINT 后，huggingface_hub / timm 都会自动改走镜像
    HF_ENDPOINT: str = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")
    HUGGINGFACE_HUB_ENDPOINT: str = (
        os.getenv("HUGGINGFACE_HUB_ENDPOINT")
        or os.getenv("HF_ENDPOINT", "https://hf-mirror.com")
    )
    HF_HOME: Optional[str] = os.getenv("HF_HOME")  # 模型缓存目录
    HF_HUB_DOWNLOAD_TIMEOUT: int = int(os.getenv("HF_HUB_DOWNLOAD_TIMEOUT", "60"))
    # Windows 上创建符号链接需要 Developer Mode/管理员, 否则 huggingface_hub 会
    # 抛 [WinError 14007]。这两个开关强制走 copy 模式 + 静音警告。
    HF_HUB_DISABLE_SYMLINKS_WARNING: bool = (
        os.getenv("HF_HUB_DISABLE_SYMLINKS_WARNING", "0").lower()
        in ("1", "true", "yes", "on")
    )
    HF_HUB_DISABLE_SYMLINKS: bool = (
        os.getenv("HF_HUB_DISABLE_SYMLINKS", "0").lower()
        in ("1", "true", "yes", "on")
    )

    # ===== CORS =====
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")  # 逗号分隔或 *
    # v2.5.15 P1-3: 显式化 CORS credentials 配置
    # 旧: main.py 写死 True, 与 CORS_ORIGINS="*" 互斥
    # 新: 独立配置 + validator 检查
    CORS_ALLOW_CREDENTIALS: bool = os.getenv("CORS_ALLOW_CREDENTIALS", "true").lower() == "true"

    # env_file 用绝对路径：避免 uv run 在项目根目录时 cwd != backend 找不到 .env
    # 文件固定位于 backend/.env（与本文件同级的上一级）
    _ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ===== 派生属性（向后兼容） =====

    @property
    def EFFECTIVE_JWT_SECRET(self) -> str:
        """JWT 签名密钥，兼容 SECRET_KEY / JWT_SECRET 两种命名
        v2.5.15 P1-1: 硬编码兜底字符串保留作 attribute, 但生产环境
        _validate_production_secrets 会先 fail 永远走不到. 开发环境
        .env 配了 SECRET_KEY 也不会用到这里.
        """
        return (
            self.SECRET_KEY
            or self.JWT_SECRET
            or "change-me-to-a-random-string-min-32-chars"
        )

    # v2.5.15 P1-1 + P1-2: 生产环境 fail-fast 校验
    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """生产环境强校验, 开发环境 WARN 不阻塞

        - SECRET_KEY (or JWT_SECRET) 必须设置
        - MYSQL_PASSWORD 不能用默认值 (root123/root/空)
        - APP_ENV=production 任意一条违反则 ValueError, 启动失败
        - APP_ENV=development 仅 WARN, 不影响开发体验
        """
        issues: list[str] = []
        if not (self.SECRET_KEY or self.JWT_SECRET):
            issues.append(
                "SECRET_KEY (or JWT_SECRET) is not set. "
                "JWT tokens can be forged."
            )
        # 拦截不安全的默认密码
        _insecure = ("root123", "", "password", "root")
        if self.MYSQL_PASSWORD in _insecure:
            issues.append(
                f"MYSQL_PASSWORD uses default/insecure value: {self.MYSQL_PASSWORD!r}"
            )

        if not issues:
            return self

        msg = "[CONFIG SECURITY] " + " | ".join(issues)
        if self.APP_ENV == "production":
            # 生产环境直接抛错, 阻止启动
            raise ValueError(msg)
        # 开发/测试环境: WARN 提示, 不阻塞
        import warnings
        warnings.warn(msg, stacklevel=2)
        return self

    @property
    def EFFECTIVE_MYSQL_DB(self) -> str:
        return self.MYSQL_DB or self.MYSQL_DATABASE

    @property
    def EFFECTIVE_DATABASE_URL(self) -> str:
        """直传 DATABASE_URL（测试），或拼装 MySQL 连接串"""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"mysql+aiomysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.EFFECTIVE_MYSQL_DB}"
        )

    @property
    def REDIS_URL(self) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def CELERY_BROKER(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def CELERY_BACKEND(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL

    @property
    def EFFECTIVE_BASE_MODEL(self) -> str:
        return self.DEFAULT_BASE_MODEL or self.DEFAULT_MODEL

    @property
    def CORS_ORIGINS_LIST(self) -> List[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def MAX_UPLOAD_SIZE_BYTES(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


settings = Settings()
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.MODEL_DIR.mkdir(parents=True, exist_ok=True)

# ---- 在最早时机同步关键环境变量到 os.environ ----
# config.py 是 app 启动时第一个被 import 的模块，pydantic-settings 自动从 .env
# 加载到这里，但很多子模块（如 app.core.redis_client, app.core.minio_client）
# 直接用 os.getenv(...) 读 OS 环境变量，如果 OS 环境没有 9770 就会用默认 6379。
# 这里把 .env 的关键端口/host 写回 os.environ，让所有子模块看到一致配置。
import os as _os
_ENV_SYNC_KEYS = [
    "REDIS_HOST", "REDIS_PORT", "REDIS_DB", "REDIS_PASSWORD",
    "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE",
    "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_BUCKET", "MINIO_SECURE",
    "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND",
    "SECRET_KEY",
]
for _k in _ENV_SYNC_KEYS:
    _v = getattr(settings, _k, None)
    if _v is not None and _v != "":
        _os.environ[_k] = str(_v)

# ---- 在最早时机设置 HuggingFace 镜像环境变量 ----
# 必须在 huggingface_hub / timm 被 import 之前完成设置。
# config.py 会在 app.main 启动时第一个被 import，所以这里设置对全局生效。
if settings.HF_ENDPOINT:
    _os.environ["HF_ENDPOINT"] = settings.HF_ENDPOINT
    _os.environ["HUGGINGFACE_HUB_ENDPOINT"] = settings.HUGGINGFACE_HUB_ENDPOINT
if settings.HF_HOME:
    _os.environ["HF_HOME"] = str(settings.HF_HOME)
    # 立刻创建目录
    Path(settings.HF_HOME).mkdir(parents=True, exist_ok=True)
_os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", str(settings.HF_HUB_DOWNLOAD_TIMEOUT))
# ---- 在最早时机禁用 HF symlink (Windows 上 [WinError 14007] 根因) ----
# 必须比 huggingface_hub.constants 被读取更早, 所以这里只写 os.environ,
# constants 模块会在 main.py / tasks.py 显式 set 兜底。
_os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1" if settings.HF_HUB_DISABLE_SYMLINKS_WARNING else "0"
_os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1" if settings.HF_HUB_DISABLE_SYMLINKS else "0"

# ---- 兜底: 显式 import huggingface_hub.constants 并 set ----
# 此时 huggingface_hub 可能还没被任何业务模块 import, 这里提前 import
# 并写常量, 避免后续 timm / transformers 内部缓存读到旧值。
try:
    import huggingface_hub.constants as _hf_const
    _hf_const.HF_HUB_DISABLE_SYMLINKS = bool(settings.HF_HUB_DISABLE_SYMLINKS)
    _hf_const.HF_HUB_DISABLE_SYMLINKS_WARNING = bool(settings.HF_HUB_DISABLE_SYMLINKS_WARNING)
except Exception:  # huggingface_hub 还没装/版本不兼容时跳过
    pass
