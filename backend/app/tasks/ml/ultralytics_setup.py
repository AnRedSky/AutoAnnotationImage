"""
v2.5.29: ultralytics 路径集中配置
=================================

**v3.0.0 迁移**: 从 app.core.ultralytics_setup 迁入 app.tasks.ml.ultralytics_setup
(与 detection/segmentation/classification 平级, 属于 ML 训练基础设施).

为什么需要:
- ultralytics 8.x 不读 ULTRALYTICS_HOME 来定位 weights_dir, 而是读
  $YOLO_CONFIG_DIR/settings.yaml (默认 %APPDATA%/Ultralytics/settings.yaml).
- 用户机器上的全局 settings.yaml 里 weights_dir 经常指向已卸载的第三方工具路径
  (例如 D:\\ProgramFiles\\OpenSoftware\\ComfyUI-aki-v1.5\\weights).
- ultralytics 找不到 weights_dir 下的模型时, 会回退到当前工作目录 (backend/) 下载,
  导致 yolov8n.pt / yolov8s.pt 等基础权重直接落到 backend/ 根目录, 污染项目目录.

修复:
1. 在 import ultralytics 之前设置 YOLO_CONFIG_DIR → 项目内 backend/models/cache/ultralytics/
   (settings.yaml 写到项目内, 不再读全局脏配置)
2. 显式把 ultralytics settings.weights_dir / runs_dir / datasets_dir 三个字段
   全部覆盖到 backend/models/cache/ultralytics/ 下的子目录.
3. YOLO("yolov8n.pt") 找不到时会自动下载到 weights_dir (项目内), 不再落到 cwd.

使用:
    from app.tasks.ml.ultralytics_setup import configure_ultralytics
    configure_ultralytics()  # 必须在 import ultralytics 之前调用
"""
from __future__ import annotations

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def configure_ultralytics() -> None:
    """
    在 worker / API 启动最早阶段调用一次, 把 ultralytics 三个目录全部锁到项目内.

    调用前必须保证已 import app.config.settings (否则读不到路径).
    调用后:
    - os.environ['YOLO_CONFIG_DIR'] = <项目内 ultralytics 根>
    - os.environ['ULTRALYTICS_HOME'] = 同上
    - ultralytics.settings.weights_dir / runs_dir / datasets_dir 全部覆盖到
      <ULTRALYTICS_HOME>/weights 等子目录
    """
    try:
        from app.core.config import settings
    except Exception as e:
        # 在早期模块加载阶段 settings 还没初始化, 直接跳过
        logger.debug(f"[ultralytics_setup] skip, settings not ready: {e}")
        return

    yolo_cfg = settings.YOLO_CONFIG_DIR
    weights_dir = settings.ULTRALYTICS_WEIGHTS_DIR
    runs_dir = settings.ULTRALYTICS_RUNS_DIR
    datasets_dir = settings.ULTRALYTICS_DATASETS_DIR

    # 1) 写环境变量: 必须在 import ultralytics 之前, 否则 ultralytics.utils.__init__
    #    第一次 import 时 USER_CONFIG_DIR 就被锁到 %APPDATA%/Ultralytics 了
    os.environ["YOLO_CONFIG_DIR"] = str(yolo_cfg)
    os.environ["ULTRALYTICS_HOME"] = str(settings.ULTRALYTICS_HOME)
    # 兼容老 env (与 tasks.py 原逻辑一致, 不破坏现有)
    os.environ.setdefault("ULTRALYTICS_HOME", str(settings.ULTRALYTICS_HOME))

    # 2) 预先创建子目录, 避免 ultralytics 第一次写入时报权限/不存在错误
    for d in (yolo_cfg, weights_dir, runs_dir, datasets_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"[ultralytics_setup] mkdir {d} failed: {e}")

    # 3) 覆盖 ultralytics settings (在已经 import 过的场景下也能生效)
    try:
        from ultralytics import settings as _u_settings
        _u_settings.update({
            "weights_dir": str(weights_dir),
            "runs_dir": str(runs_dir),
            "datasets_dir": str(datasets_dir),
        })
        # 关闭 sync (避免每次启动上报匿名统计)
        try:
            _u_settings.update({"sync": False})
        except Exception:
            pass
        logger.info(
            f"[ultralytics_setup] OK: weights_dir={weights_dir} "
            f"runs_dir={runs_dir} datasets_dir={datasets_dir}"
        )
    except ImportError:
        # ultralytics 还没装, 下次再配置
        logger.debug("[ultralytics_setup] ultralytics not yet installed, defer")
    except Exception as e:
        logger.warning(f"[ultralytics_setup] settings.update failed: {e}")


def migrate_legacy_yolo_weights(backend_root: Path | None = None) -> int:
    """
    v2.5.29: 把历史下载的 yolov8*.pt 迁到 ULTRALYTICS_WEIGHTS_DIR.
    v2.5.30: 增量兼容 - 既扫 backend/ 根, 也扫 backend/models/cache/ultralytics/weights/
            (旧版本配置时, ultralytics 会把 .pt 落到这两个位置之一).

    **v3.0.0 适配**: 文件位置从 app/core/ 变为 app/tasks/ml/, 路径计算从
    `.parent.parent.parent` (3 层回退) 改为 `.parent.parent.parent.parent` (4 层回退).

    Returns:
        实际迁移的文件数
    """
    from app.core.config import settings

    target = settings.ULTRALYTICS_WEIGHTS_DIR
    target.mkdir(parents=True, exist_ok=True)
    # 默认 backend/ 根目录
    if backend_root is None:
        # app/tasks/ml/ultralytics_setup.py → app/tasks/ml → app/tasks → app → backend
        backend_root = Path(__file__).resolve().parent.parent.parent.parent

    moved = 0
    # 1) backend/ 根目录下的 yolov8*.pt
    # 2) backend/models/cache/ultralytics/weights/yolov8*.pt (旧配置时的落点)
    candidates_dirs = [
        backend_root,
        backend_root / "models" / "cache" / "ultralytics" / "weights",
    ]
    seen: set = set()
    for d in candidates_dirs:
        if not d.exists():
            continue
        for src in d.glob("yolov8*.pt"):
            if not src.is_file():
                continue
            # 同源文件不要重复处理
            try:
                key = src.resolve()
            except OSError:
                key = src
            if key in seen:
                continue
            seen.add(key)

            dst = target / src.name
            if dst.exists():
                # 目标已有同文件 (例如之前就预置过的), 仅删源
                try:
                    src.unlink()
                    moved += 1
                    logger.info(f"[ultralytics_setup] 已删除历史残留: {src} (cache 已有同文件)")
                except OSError as e:
                    logger.warning(f"[ultralytics_setup] 删除 {src} 失败: {e}")
                continue
            try:
                import shutil
                shutil.move(str(src), str(dst))
                moved += 1
                logger.info(f"[ultralytics_setup] 迁移 {src} → {dst}")
            except OSError as e:
                logger.warning(f"[ultralytics_setup] 迁移 {src} 失败: {e}")
    return moved
