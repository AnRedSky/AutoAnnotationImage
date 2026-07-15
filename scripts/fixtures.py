"""
Test Image Fixtures Generator
=============================
生成一组可复用的 E2E 测试图片（PNG/JPG），保存到 backend/tests/fixtures/images/。

设计原则:
- 文件名稳定 (fixture_01.png ... fixture_10.png)，多次运行覆盖即可
- 颜色/尺寸/格式各异，模拟真实数据集
- 不依赖外部资源 (HuggingFace / CIFAR-10)，离线可生成

用法:
    python scripts/fixtures.py             # 缺则生成，已存在则跳过
    python scripts/fixtures.py --force     # 强制重新生成
    python scripts/fixtures.py --clean     # 删除后再生成
    python scripts/fixtures.py --info      # 只打印清单
"""
import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "backend" / "tests" / "fixtures" / "images"
FIXTURE_META = ROOT / "backend" / "tests" / "fixtures" / "manifest.json"

# 10 张图: 颜色/尺寸各不相同, 模拟真实样本
FIXTURES = [
    # (filename,         width, height, color RGB,        format)
    ("fixture_01.png",     32,    32,    (220,  20,  60), "png"),
    ("fixture_02.png",     64,    64,    ( 30, 144, 255), "png"),
    ("fixture_03.png",    128,    96,    ( 50, 205,  50), "png"),
    ("fixture_04.png",    128,   128,    (255, 165,   0), "png"),
    ("fixture_05.png",    256,   192,    (148,   0, 211), "png"),
    ("fixture_06.png",    320,   240,    (255, 215,   0), "png"),
    ("fixture_07.png",    480,   320,    (  0, 206, 209), "png"),
    ("fixture_08.png",    512,   384,    (199,  21, 133), "png"),
    ("fixture_09.jpg",    224,   224,    ( 72, 209, 204), "jpg"),
    ("fixture_10.jpg",    640,   480,    (255,  99,  71), "jpg"),
]


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)


def _make_png(path: Path, width: int, height: int, color: tuple) -> int:
    """最小可用 PNG (RGB, 8-bit)，不依赖 PIL。返回字节数。"""
    r, g, b = color
    raw = b""
    for y in range(height):
        raw += b"\x00"  # filter: None
        # 每行用两种颜色交错画条纹，便于调试
        c1 = bytes((r, g, b))
        c2 = bytes(((r + 60) & 0xFF, (g + 60) & 0xFF, (b + 60) & 0xFF))
        for x in range(width):
            raw += c1 if (x + y) % 2 == 0 else c2
    compressed = zlib.compress(raw, level=6)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        f.write(_png_chunk(b"IDAT", compressed))
        f.write(_png_chunk(b"IEND", b""))
    return path.stat().st_size


def _make_jpg(path: Path, width: int, height: int, color: tuple) -> int:
    """用 Pillow 生成 JPG（更接近真实场景）。失败时回退 PNG。"""
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (width, height), color)
        # 画一些几何图形让它"看起来不一样"
        draw = ImageDraw.Draw(img)
        cx, cy = width // 2, height // 2
        draw.rectangle([10, 10, width - 10, height - 10], outline=(255, 255, 255), width=2)
        draw.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], fill=(0, 0, 0))
        img.save(path, "JPEG", quality=85)
        return path.stat().st_size
    except ImportError:
        # 无 Pillow: 改成 PNG 同名
        png_path = path.with_suffix(".png")
        size = _make_png(png_path, width, height, color)
        png_path.rename(path)  # 实际还是 PNG 内容但后缀为 jpg - 不推荐
        # 保险起见改为真 PNG
        new_path = path.with_name(path.stem + ".png")
        png_path.rename(new_path)
        return new_path.stat().st_size


def _make_image(path: Path, width: int, height: int, color: tuple, fmt: str) -> int:
    if fmt == "png":
        return _make_png(path, width, height, color)
    if fmt == "jpg":
        return _make_jpg(path, width, height, color)
    raise ValueError(f"Unknown format: {fmt}")


def generate(force: bool = False, clean: bool = False) -> list:
    """生成全部 fixture 图片，返回 manifest 列表。"""
    if clean and FIXTURE_DIR.exists():
        for f in FIXTURE_DIR.iterdir():
            f.unlink()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for filename, w, h, color, fmt in FIXTURES:
        path = FIXTURE_DIR / filename
        if path.exists() and not force:
            size = path.stat().st_size
        else:
            size = _make_image(path, w, h, color, fmt)
        manifest.append({
            "filename": filename,
            "width": w,
            "height": h,
            "color": list(color),
            "format": path.suffix.lstrip(".").lower(),
            "size_bytes": size,
            "path": str(path.relative_to(ROOT)),
        })
    FIXTURE_META.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_META.write_text(
        json.dumps({"version": 1, "count": len(manifest), "items": manifest}, indent=2),
        encoding="utf-8",
    )
    return manifest


def info() -> list:
    """返回 manifest (没有则生成)。"""
    if not FIXTURE_META.exists():
        return generate()
    return json.loads(FIXTURE_META.read_text(encoding="utf-8"))["items"]


def main():
    ap = argparse.ArgumentParser(description="Generate test image fixtures")
    ap.add_argument("--force", action="store_true", help="Overwrite existing fixtures")
    ap.add_argument("--clean", action="store_true", help="Delete and regenerate")
    ap.add_argument("--info", action="store_true", help="Print manifest only")
    args = ap.parse_args()

    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    if args.info:
        items = info()
        print(f"Fixtures dir: {FIXTURE_DIR}")
        print(f"Count: {len(items)}")
        for it in items:
            print(f"  - {it['filename']:20s} {it['width']}x{it['height']}  {it['size_bytes']:>7d} B  {it['format']}")
        return

    items = generate(force=args.force, clean=args.clean)
    total_bytes = sum(it["size_bytes"] for it in items)
    print(f"[OK] Generated {len(items)} fixtures in {FIXTURE_DIR}")
    print(f"     Total size: {total_bytes} bytes ({total_bytes / 1024:.1f} KB)")
    print(f"     Manifest:   {FIXTURE_META.relative_to(ROOT)}")
    for it in items:
        print(f"       {it['filename']:20s} {it['width']:>4d}x{it['height']:<4d}  {it['size_bytes']:>6d} B  {it['format']}")


if __name__ == "__main__":
    main()
