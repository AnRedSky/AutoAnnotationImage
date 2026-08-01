"""
密钥生成器 (Secrets Generator)
==================================

为统一 .env 文件生成所有强随机密钥。
替代 .env.example 中的 CHANGEME 占位符。

用法:
  python scripts/gen_secrets.py                    # 输出到 stdout
  python scripts/gen_secrets.py --write            # 写入 .env (覆盖 CHANGEME)
  python scripts/gen_secrets.py --print-only SECRET_KEY  # 只生成一个

设计原则:
  - SECRET_KEY: 32 字节 url-safe 编码 (默认 48 字符)
  - *_PASSWORD: 16 字节 url-safe 编码 (默认 22 字符), 适合 MySQL/Redis/MinIO
  - MINIO_ACCESS_KEY: 8 字节 url-safe 编码 (默认 11 字符), 符合 MinIO 用户名规范
"""
import argparse
import re
import secrets
import sys
from pathlib import Path


def gen_secret_key() -> str:
    """32 字节 url-safe, 适合 SECRET_KEY"""
    return secrets.token_urlsafe(32)


def gen_password() -> str:
    """16 字节 url-safe, 适合 MySQL/Redis/MinIO 密码"""
    return secrets.token_urlsafe(16)


def gen_minio_user() -> str:
    """8 字节 url-safe, 适合 MinIO 用户名 (3-20 字符限制)"""
    return secrets.token_urlsafe(8)


def replace_changeme_in_file(env_path: Path, replacements: dict) -> int:
    """替换 .env 中的 CHANGEME 占位符"""
    if not env_path.exists():
        print(f"[ERR] 找不到文件: {env_path}", file=sys.stderr)
        return 1
    content = env_path.read_text(encoding="utf-8")
    count = 0
    for key, value in replacements.items():
        # 匹配 CHANGEME-xxx-CHANGEME 形式
        pattern = rf"(^{re.escape(key)}=)CHANGEME-[^\n]*CHANGEME"
        new_content, n = re.subn(pattern, rf"\g<1>{value}", content, flags=re.MULTILINE)
        if n > 0:
            content = new_content
            count += n
            print(f"  ✓ {key}: 替换了 {n} 处")
        else:
            print(f"  ⚠ {key}: 未找到 CHANGEME 占位符 (可能已自定义)")
    env_path.write_text(content, encoding="utf-8")
    return count


def main():
    ap = argparse.ArgumentParser(description="为 .env 生成强随机密钥")
    ap.add_argument(
        "--write",
        action="store_true",
        help="写入 .env (项目根), 覆盖 CHANGEME 占位符",
    )
    ap.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="要写入的 .env 路径 (默认项目根 .env)",
    )
    ap.add_argument(
        "--print-only",
        choices=["SECRET_KEY", "MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD",
                 "REDIS_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"],
        help="只打印指定字段",
    )
    args = ap.parse_args()

    if args.print_only:
        gen_map = {
            "SECRET_KEY": gen_secret_key,
            "MYSQL_PASSWORD": gen_password,
            "MYSQL_ROOT_PASSWORD": gen_password,
            "REDIS_PASSWORD": gen_password,
            "MINIO_ACCESS_KEY": gen_minio_user,
            "MINIO_SECRET_KEY": gen_password,
        }
        print(gen_map[args.print_only]())
        return 0

    # 1) 生成所有密钥
    print()
    print("=" * 60)
    print("  强随机密钥生成器")
    print("=" * 60)
    print()

    replacements = {
        "SECRET_KEY": gen_secret_key(),
        "MYSQL_PASSWORD": gen_password(),
        "MYSQL_ROOT_PASSWORD": gen_password(),
        "REDIS_PASSWORD": gen_password(),
        "MINIO_ACCESS_KEY": gen_minio_user(),
        "MINIO_SECRET_KEY": gen_password(),
    }

    print("生成的密钥 (写入前请妥善保管):")
    print()
    for k, v in replacements.items():
        print(f"  {k}={v}")
    print()

    if not args.write:
        print("提示: 用 --write 直接写入 .env")
        print("      或复制上方到 .env 文件")
        return 0

    # 2) 替换 .env 中的 CHANGEME
    env_path = args.env_file
    if not env_path.exists():
        # 提示用户先复制 .env.example
        example = env_path.parent / ".env.example"
        if example.exists():
            print(f"[INFO] {env_path} 不存在, 从 {example} 复制")
            env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            print(f"[ERR] 找不到 {env_path} 或 {example}", file=sys.stderr)
            return 1

    print(f"正在替换 {env_path} 中的 CHANGEME 占位符:")
    print()
    n = replace_changeme_in_file(env_path, replacements)

    # 额外: 替换 URL 中的 redis 密码占位符
    content = env_path.read_text(encoding="utf-8")
    redis_pwd = replacements["REDIS_PASSWORD"]
    new_content = content.replace(
        f"redis://:CHANGEME-redis-password-CHANGEME@",
        f"redis://:{redis_pwd}@"
    )
    if new_content != content:
        env_path.write_text(new_content, encoding="utf-8")
        print(f"  ✓ CELERY_BROKER_URL / CELERY_RESULT_BACKEND 中的 redis 密码已替换")
        n += 2

    print()
    print(f"[OK] 已替换 {n} 个占位符")
    print()
    print("⚠ 安全提醒:")
    print("  - .env 已自动加入 .gitignore, 不会被提交")
    print("  - 请妥善保管 .env, 不要泄露到公开渠道")
    print("  - 定期轮换密钥 (建议 90 天)")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
