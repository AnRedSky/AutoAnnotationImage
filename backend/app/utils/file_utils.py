"""
File Utilities (Utils Layer)
===========================

文件操作通用工具. 集中处理:
- 文件大小格式化 (1024 B → "1.5 MB")
- 安全文件名清理 (去除路径分隔符, 特殊字符)
- 路径解析 (相对 → 绝对, 项目根锚定)
- MIME 类型推断
- 文件哈希 (MD5/SHA256, 用于去重)

**v3.0.0 Stage 3 新增**.

**依赖**: 纯标准库 + pathlib, 无第三方依赖.
"""
import hashlib
import mimetypes
import re
import unicodedata
from pathlib import Path
from typing import Optional, Union

# 文件大小单位 (二进制)
_SIZE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def format_size(size_bytes: Optional[Union[int, float]]) -> str:
    """字节数 → 人类可读大小 (e.g. 1536 → "1.5 KB")

    Args:
        size_bytes: 字节数 (允许 None)

    Returns:
        格式化字符串, e.g. "1.5 MB" / "512 B" / "2.3 GB"
    """
    if size_bytes is None or size_bytes < 0:
        return "-"
    size = float(size_bytes)
    for unit in _SIZE_UNITS:
        if size < 1024.0:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} {_SIZE_UNITS[-1]}"


_SAFE_FILENAME_RE = re.compile(r"[^\w\s.\-]")


def safe_filename(name: str, max_length: int = 200) -> str:
    """清理文件名, 去除路径分隔符和危险字符

    Args:
        name: 原始文件名
        max_length: 最大长度 (默认 200, 避免超长文件名)

    Returns:
        清理后的安全文件名, e.g. "测试图片_001.jpg"

    Examples:
        >>> safe_filename("../../../etc/passwd")
        'etcpasswd'
        >>> safe_filename("测试/图片*001?.jpg")
        '测试图片001.jpg'
    """
    if not name:
        return "unnamed"
    # 1) 路径分隔符直接去掉
    name = name.replace("/", "_").replace("\\", "_")
    # 2) Unicode 规范化 (NFKC: 全角 → 半角, 兼容字符)
    name = unicodedata.normalize("NFKC", name)
    # 3) 去除除 \w (字母数字下划线) / 空白 / . - 之外的字符
    name = _SAFE_FILENAME_RE.sub("", name)
    # 4) 去除前后空白和点
    name = name.strip().strip(".")
    # 5) 限制长度 (保留扩展名)
    if len(name) > max_length:
        stem = Path(name).stem[: max_length - 10]
        suffix = Path(name).suffix
        name = stem + suffix
    return name or "unnamed"


def guess_mime_type(filename: str) -> str:
    """根据文件名猜测 MIME 类型

    Args:
        filename: 文件名 (含扩展名)

    Returns:
        MIME 字符串, e.g. "image/jpeg" / "application/octet-stream" (未知)
    """
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def file_md5(path: Union[str, Path], chunk_size: int = 8192 * 1024) -> str:
    """计算文件 MD5 (流式读取, 适合大文件)

    Args:
        path: 文件路径
        chunk_size: 每次读取的字节数 (默认 8MB, 大文件友好)

    Returns:
        32 字符 MD5 十六进制字符串
    """
    md5 = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            md5.update(chunk)
    return md5.hexdigest()


def file_sha256(path: Union[str, Path], chunk_size: int = 8192 * 1024) -> str:
    """计算文件 SHA256 (流式读取, 适合大文件)"""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def ensure_dir(path: Union[str, Path]) -> Path:
    """确保目录存在, 不存在则创建 (含中间目录)

    Args:
        path: 目录路径

    Returns:
        Path 对象 (绝对路径)
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


__all__ = [
    "format_size",
    "safe_filename",
    "guess_mime_type",
    "file_md5",
    "file_sha256",
    "ensure_dir",
]
