"""
Application Configuration (Core Layer)
=====================================

All configs from environment variables, with sensible defaults for dev.
兼容 .env (SECRET_KEY/MYSQL_DATABASE) 与 直传 (DATABASE_URL) 两种方式。

v3.0.0 迁移: 从 app.core.config 迁入 app.core.config (Phase 1.7)
"""
import os
from pathlib import Path
from typing import List, Optional
from pydantic import model_validator, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---- v2.5.30: 项目根目录绝对锚点 ----
# 历史问题: UPLOAD_DIR / MODEL_DIR / PRETRAINED_CACHE_DIR 等默认值是 "./uploads" "./models",
#           Path(...).resolve() 会基于 cwd 解析, 当从 backend/ 启动后端时, 路径会变成
#           backend/uploads, backend/models, 污染项目源码目录, 并与项目根目录的 models/ 重复.
# 修复: 在模块加载时计算项目根 (config.py 位于 backend/app/core/config.py → 上 3 层就是项目根),
#       全部默认值强制以项目根为基准, 与 cwd 无关. 用户在 .env 里显式设置绝对路径时仍优先用 .env.
#       用户在 .env 里写相对路径 (如 "./models") 也会基于项目根解析, 而非 cwd.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent    # backend/app/core -> backend/app
_PROJECT_ROOT = _BACKEND_ROOT.parent                             # backend
_DEFAULT_MODEL_DIR = (_PROJECT_ROOT / "models").resolve()
_DEFAULT_UPLOAD_DIR = (_PROJECT_ROOT / "uploads").resolve()
_DEFAULT_DATA_DIR = (_DEFAULT_MODEL_DIR / "data").resolve()
_DEFAULT_PRETRAINED_CACHE_DIR = (_DEFAULT_MODEL_DIR / "cache").resolve()
_DEFAULT_ULTRALYTICS_HOME = (_DEFAULT_PRETRAINED_CACHE_DIR / "ultralytics").resolve()


def _resolve_storage_path(env_value: "Optional[str]", default_abs: Path) -> Path:
    """
    v2.5.30: 把 env 里读到的路径 + 默认值, 统一规范成"基于项目根的绝对路径".

    规则:
    - env_value 为空 (None / "" / 空白): 用 default_abs
    - env_value 是绝对路径: 原样用 (尊重用户/部署定制)
    - env_value 是相对路径: 基于项目根 (而非 cwd) 解析, 避免 backend/ 启动时
      把 ./models 解析成 backend/models
    - 统一 .resolve() 处理 ../, 符号链接, 大小写
    """
    if env_value is None or str(env_value).strip() == "":
        return Path(default_abs).resolve()
    p = Path(str(env_value).strip())
    if p.is_absolute():
        return p.resolve()
    # 相对路径: 锚到项目根, 而非 cwd
    return (_PROJECT_ROOT / p).resolve()


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

    # ===== 数据库连接池 (MySQL) =====
    # 原 database.py 硬编码 pool_size=10 / max_overflow=20, 现外置为配置
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "10"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "20"))
    # 连接主动回收周期(秒). MySQL 默认 wait_timeout=8h, 长连接静默断开后首次
    # 请求会报错, 设 3600s 主动回收 + pool_pre_ping 双保险
    DB_POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "3600"))

    # ===== Redis =====
    REDIS_HOST: str = os.getenv("REDIS_HOST", "127.0.0.1")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    REDIS_PASSWORD: Optional[str] = os.getenv("REDIS_PASSWORD") or None

    CELERY_BROKER_URL: Optional[str] = os.getenv("CELERY_BROKER_URL")
    CELERY_RESULT_BACKEND: Optional[str] = os.getenv("CELERY_RESULT_BACKEND")

    # ===== Celery Worker 并发配置 =====
    # pool: solo (1 进程 1 任务, Windows 最稳) | threads (1 进程 N 线程, I/O 密集友好) |
    #       prefork (N 进程, Linux only) | gevent (需装 gevent)
    # Windows 上 prefork 不可用, 推荐 threads; 若要保留旧行为设 CELERY_WORKER_POOL=solo
    CELERY_WORKER_POOL: str = os.getenv("CELERY_WORKER_POOL", "threads")
    # concurrency: solo 池下被忽略; threads 池下表示同时跑的线程数
    # 设太大时 CPU 训练任务会互踩, I/O 自动标注任务可适当调高
    CELERY_WORKER_CONCURRENCY: int = int(os.getenv("CELERY_WORKER_CONCURRENCY", "2"))

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
    # v2.5.30: 强制以项目根为基准的绝对路径, 不再依赖 cwd. .env 里若写了绝对路径仍优先用 .env;
    #          .env 里若写的是相对路径 (如 "./models"), 也会基于项目根解析, 而非 cwd.
    #          解决: 从 backend/ 启动时, 老的 "./models" 不再变成 backend/models.
    UPLOAD_DIR: Path = _resolve_storage_path(os.getenv("UPLOAD_DIR"), _DEFAULT_UPLOAD_DIR)
    MODEL_DIR: Path = _resolve_storage_path(os.getenv("MODEL_DIR"), _DEFAULT_MODEL_DIR)
    # 临时训练数据根目录 (YOLO 数据集导出等), 默认 MODEL_DIR/data
    DATA_DIR: Path = _resolve_storage_path(os.getenv("DATA_DIR"), _DEFAULT_DATA_DIR)
    # 预训练权重统一缓存根目录 (timm/torchvision/ultralytics), 默认 MODEL_DIR/cache
    PRETRAINED_CACHE_DIR: Path = _resolve_storage_path(
        os.getenv("PRETRAINED_CACHE_DIR"), _DEFAULT_PRETRAINED_CACHE_DIR
    )
    # ---- v2.5.29: ultralytics 单独子目录 (YOLO_CONFIG_DIR / weights_dir / runs_dir 统一指向这) ----
    ULTRALYTICS_HOME: Path = _resolve_storage_path(
        os.getenv("ULTRALYTICS_HOME"), _DEFAULT_ULTRALYTICS_HOME
    )
    ULTRALYTICS_WEIGHTS_DIR: Path = _resolve_storage_path(
        os.getenv("ULTRALYTICS_WEIGHTS_DIR"), _DEFAULT_ULTRALYTICS_HOME / "weights"
    )
    ULTRALYTICS_RUNS_DIR: Path = _resolve_storage_path(
        os.getenv("ULTRALYTICS_RUNS_DIR"), _DEFAULT_ULTRALYTICS_HOME / "runs"
    )
    ULTRALYTICS_DATASETS_DIR: Path = _resolve_storage_path(
        os.getenv("ULTRALYTICS_DATASETS_DIR"), _DEFAULT_ULTRALYTICS_HOME / "datasets"
    )
    # YOLO_CONFIG_DIR: ultralytics 把 settings.yaml 写到这里; 默认 ULTRALYTICS_HOME 根目录
    YOLO_CONFIG_DIR: Path = _resolve_storage_path(
        os.getenv("YOLO_CONFIG_DIR"), _DEFAULT_ULTRALYTICS_HOME
    )
    MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "20"))

    # ===== Stage 3: 请求耗时中间件 =====
    # 慢请求阈值 (毫秒), 超过则 WARNING 日志
    REQUEST_SLOW_THRESHOLD_MS: int = int(os.getenv("REQUEST_SLOW_THRESHOLD_MS", "500"))

    # ===== Stage 5: 业务缓存层 =====
    # 缓存总开关 (生产可关闭, 开发默认开启)
    CACHE_ENABLED: bool = os.getenv("CACHE_ENABLED", "true").lower() in ("1", "true", "yes", "on")
    # 默认 TTL (秒)
    CACHE_DEFAULT_TTL: int = int(os.getenv("CACHE_DEFAULT_TTL", "300"))
    # key 前缀 (避免多服务共用 Redis 时冲突)
    CACHE_KEY_PREFIX: str = os.getenv("CACHE_KEY_PREFIX", "app:")
    # 慢 SQL 阈值 (毫秒)
    SQL_SLOW_THRESHOLD_MS: int = int(os.getenv("SQL_SLOW_THRESHOLD_MS", "200"))

    # ===== ML =====
    DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", "efficientnet_b0")
    DEFAULT_BASE_MODEL: Optional[str] = None  # 兼容旧名
    DEFAULT_CONFIDENCE_THRESHOLD: float = float(os.getenv("DEFAULT_CONFIDENCE_THRESHOLD", "0.6"))
    INFERENCE_DEVICE: str = os.getenv("INFERENCE_DEVICE", "cpu")  # cpu | cuda

    # ===== HuggingFace 镜像（国内网络环境必须配）=====
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
    CORS_ALLOW_CREDENTIALS: bool = os.getenv("CORS_ALLOW_CREDENTIALS", "true").lower() == "true"

    # env_file 用绝对路径：避免 uv run 在项目根目录时 cwd != backend 找不到 .env
    # 文件固定位于 backend/.env（与本文件同级的上一级)
    _ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ---- v2.5.30: 路径字段后处理 ----
    _STORAGE_PATH_FIELDS = (
        "UPLOAD_DIR", "MODEL_DIR", "DATA_DIR", "PRETRAINED_CACHE_DIR",
        "ULTRALYTICS_HOME", "ULTRALYTICS_WEIGHTS_DIR", "ULTRALYTICS_RUNS_DIR",
        "ULTRALYTICS_DATASETS_DIR", "YOLO_CONFIG_DIR",
    )

    @field_validator(*_STORAGE_PATH_FIELDS)
    @classmethod
    def _normalize_storage_path(cls, v):
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return v
        p = Path(v) if not isinstance(v, Path) else v
        if p.is_absolute():
            return p.resolve()
        # 相对路径: 锚到项目根, 而非 cwd
        return (_PROJECT_ROOT / p).resolve()

    # ===== 派生属性（向后兼容） =====

    @property
    def EFFECTIVE_JWT_SECRET(self) -> str:
        """JWT 签名密钥，兼容 SECRET_KEY / JWT_SECRET 两种命名

        v3.0.0 审查修复 P0-7: 移除占位字符串兜底
        - 旧: SECRET_KEY 缺失时返回 'change-me-to-a-random-string-min-32-chars'
          (生产未设 SECRET_KEY 会用此占位, JWT 可被伪造)
        - 新: 缺失时 raise ValueError, _validate_production_secrets 已先 fail
        - dev 环境仍保留 _validate_production_secrets 的 WARN, 但 EFFECTIVE_JWT_SECRET
          现在也用相同逻辑 (确保 dev 启动能成功, 但 secret 已从 .env 读取)
        """
        if not (self.SECRET_KEY or self.JWT_SECRET):
            raise ValueError(
                "SECRET_KEY (or JWT_SECRET) is required. "
                "JWT tokens cannot be signed without a secret."
            )
        return self.SECRET_KEY or self.JWT_SECRET

    # v2.5.15 P1-1 + P1-2: 生产环境 fail-fast 校验
    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """生产环境强校验, 开发环境 WARN 不阻塞

        - SECRET_KEY (or JWT_SECRET) 必须设置 (v3.0.0 审查修复 P0-7: 也禁止占位字符串)
        - MYSQL_PASSWORD 不能用默认值 (root123/root/空)
        - APP_ENV=production 任意一条违反则 ValueError, 启动失败
        - APP_ENV=development 仅 WARN, 不影响开发体验
        """
        issues: list[str] = []
        _weak_secrets = (
            "",
            "change-me",
            "changeme",
            "secret",
            "password",
            "12345678",
            "your-secret-key",
        )
        eff_secret = self.SECRET_KEY or self.JWT_SECRET or ""
        if not eff_secret:
            issues.append(
                "SECRET_KEY (or JWT_SECRET) is not set. "
                "JWT tokens can be forged."
            )
        elif any(w in eff_secret.lower() for w in _weak_secrets):
            issues.append(
                f"SECRET_KEY looks like a weak/placeholder value: {eff_secret[:8]}... "
                "JWT tokens can be forged. Generate a strong random secret (>= 32 chars)."
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
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.PRETRAINED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
# v2.5.29: ultralytics 子目录统一预先创建, worker 启动时 settings.weights_dir 即指向这里
settings.ULTRALYTICS_HOME.mkdir(parents=True, exist_ok=True)
settings.ULTRALYTICS_WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
settings.ULTRALYTICS_RUNS_DIR.mkdir(parents=True, exist_ok=True)
settings.ULTRALYTICS_DATASETS_DIR.mkdir(parents=True, exist_ok=True)
settings.YOLO_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

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
    "CELERY_WORKER_POOL", "CELERY_WORKER_CONCURRENCY",
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

# ---- 统一预训练权重缓存到 PRETRAINED_CACHE_DIR ----
# timm (HF_HOME) / torchvision (TORCH_HOME) / ultralytics (ULTRALYTICS_HOME)
# 三套缓存统一收纳到 MODEL_DIR/cache 子目录, 便于备份/迁移/清理.
# HF_HOME: 用户显式设置则用用户的, 否则默认 PRETRAINED_CACHE_DIR/huggingface
_hf_home = settings.HF_HOME or str(settings.PRETRAINED_CACHE_DIR / "huggingface")
_os.environ["HF_HOME"] = _hf_home
Path(_hf_home).mkdir(parents=True, exist_ok=True)
# torchvision 权重缓存 (默认 ~/.cache/torch)
_torch_home = str(settings.PRETRAINED_CACHE_DIR / "torch")
_os.environ["TORCH_HOME"] = _torch_home
Path(_torch_home).mkdir(parents=True, exist_ok=True)
# ultralytics 权重缓存 (默认 ~/.cache/ultralytics)
_ultralytics_home = str(settings.PRETRAINED_CACHE_DIR / "ultralytics")
_os.environ["ULTRALYTICS_HOME"] = _ultralytics_home
Path(_ultralytics_home).mkdir(parents=True, exist_ok=True)
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
