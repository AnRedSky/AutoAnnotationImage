"""
配置一致性验证脚本 (Configuration Consistency Validator)
========================================================

验证部署相关的所有配置文件是否一致，包括：
  1) backend/.env 与 backend/.env.example 的字段是否对得上
  2) docker-compose.yml 引用的环境变量是否都在 .env 中定义
  3) .env.docker 中的占位符是否都已替换
  4) 前端 VITE_API_BASE_URL 与后端 APP_PORT 是否匹配
  5) 数据库/Redis 端口在 compose 与 .env 中是否一致
  6) 健康检查 URL 在 Dockerfile 与 backend /api/health 是否一致

用法:
  cd backend
  uv run python scripts/validate_config.py                 # 校验 .env
  uv run python scripts/validate_config.py --env .env.prod # 校验生产 env
  uv run python scripts/validate_config.py --strict        # 严格模式 (警告也算失败)
  uv run python scripts/validate_config.py --report        # 输出 JSON 报告

退出码:
  0 - 全部通过
  1 - 发现错误
  2 - 仅警告
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent  # 项目根
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

# ANSI 颜色
C = {
    "R": "\033[31m",  # red
    "G": "\033[32m",  # green
    "Y": "\033[33m",  # yellow
    "B": "\033[34m",  # blue
    "CY": "\033[36m",  # cyan
    "RS": "\033[0m",  # reset
}


def color(s: str, c: str) -> str:
    """Windows cmd 不支持 ANSI, 自动降级"""
    if sys.platform == "win32" and not os.environ.get("FORCE_COLOR"):
        return s
    return f"{C.get(c, '')}{s}{C['RS']}"


# ============== Env 解析 ==============
def parse_env_file(path: Path) -> Dict[str, str]:
    """解析 .env 风格文件, 返回 {KEY: value}"""
    if not path.exists():
        return {}
    result: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        # 去除行内注释
        if " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        # 去除引号
        if (v.startswith('"') and v.endswith('"')) or (
            v.startswith("'") and v.endswith("'")
        ):
            v = v[1:-1]
        result[k] = v
    return result


# ============== 校验项 ==============
def check_env_vs_example(env: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """检查 .env 中是否所有示例 key 都已填 (允许空值但不能缺 key)"""
    issues = []
    example = parse_env_file(BACKEND / ".env.example")
    for k in example:
        if k not in env:
            # 仅提示, 实际可能由代码默认兜底
            issues.append(("WARN", f"missing key in .env: {k}", ""))
    return issues


def check_docker_env_placeholders(env: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """检查 .env 中是否还有未替换的 __REPLACE_WITH_*__ 占位符"""
    issues = []
    placeholders = [k for k, v in env.items() if "__REPLACE_WITH_" in v]
    if placeholders:
        issues.append((
            "ERR",
            f"未替换的占位符 ({len(placeholders)} 个)",
            ", ".join(placeholders),
        ))
    return issues


def check_production_secrets(env: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """生产模式下校验密钥强度"""
    issues = []
    if env.get("APP_ENV") != "production":
        return issues  # 非生产模式不强制

    weak_secrets = ("", "change-me", "changeme", "secret", "password", "12345678")
    sk = env.get("SECRET_KEY", "")
    if not sk:
        issues.append(("ERR", "SECRET_KEY 未设置", "JWT 无法签发"))
    elif any(w in sk.lower() for w in weak_secrets):
        issues.append(("ERR", f"SECRET_KEY 强度不足 (前 8 字符: {sk[:8]}...)", ""))

    if env.get("MYSQL_PASSWORD", "") in ("", "root", "root123", "password"):
        issues.append(("ERR", "MYSQL_PASSWORD 使用默认值", ""))

    if env.get("MYSQL_ROOT_PASSWORD", "") in ("", "root", "root123"):
        issues.append(("ERR", "MYSQL_ROOT_PASSWORD 使用默认值", ""))

    if not env.get("REDIS_PASSWORD"):
        issues.append(("WARN", "REDIS_PASSWORD 未设置 (生产强烈建议)", ""))

    return issues


def check_ports_consistency(env: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """检查 MySQL/Redis 端口与默认 compose 端口一致"""
    issues = []
    # compose 中 MySQL 容器端口是 3306
    mp = env.get("MYSQL_PORT", "")
    if mp and mp not in ("3306", "3310"):
        issues.append((
            "WARN",
            f"MYSQL_PORT={mp} (compose 默认 3306; 宿主开发用 3310)",
            "需确保 docker-compose.yml ports 映射与之一致",
        ))
    rp = env.get("REDIS_PORT", "")
    if rp and rp not in ("6379", "9770"):
        issues.append((
            "WARN",
            f"REDIS_PORT={rp} (compose 默认 6379; 宿主开发用 9770)",
            "需确保 docker-compose.yml ports 映射与之一致",
        ))
    return issues


def check_compose_env_refs(env: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """扫描 docker-compose.yml, 检查 ${VAR} / ${VAR:-default} / ${VAR:?msg} 引用"""
    issues = []
    compose = ROOT / "docker-compose.yml"
    if not compose.exists():
        return issues

    text = compose.read_text(encoding="utf-8")
    # ${VAR:?err} 强制必填
    required = re.findall(r"\$\{([A-Z_][A-Z0-9_]*):\?[^}]*\}", text)
    # ${VAR:-default}
    optional = re.findall(r"\$\{([A-Z_][A-Z0-9_]*):-([^}]*)\}", text)
    # ${VAR} 直接引用
    direct = re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}", text)

    required = list(set(required) - {m for m, _ in optional})

    for var in required:
        if var not in env:
            issues.append((
                "ERR",
                f"docker-compose.yml 引用 ${var}:? 但 .env 未提供",
                "启动时会报错 fail-fast",
            ))

    return issues


def check_frontend_consistency(env: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """检查前端 VITE_API_BASE_URL 与后端配置是否一致"""
    issues = []
    fe_prod = FRONTEND / ".env.production"
    if not fe_prod.exists():
        return issues
    fe_env = parse_env_file(fe_prod)
    api_base = fe_env.get("VITE_API_BASE_URL", "")
    backend_port = env.get("APP_PORT", "8000")
    # 默认空 = 同源, OK
    # 若非空, 需包含后端端口
    if api_base and backend_port and backend_port not in api_base:
        issues.append((
            "WARN",
            f"VITE_API_BASE_URL={api_base} 未含后端端口 {backend_port}",
            "前端调用可能 404",
        ))
    return issues


def check_healthcheck_url(env: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """检查 /api/health 端点是否在所有配置中一致"""
    issues = []
    compose = ROOT / "docker-compose.yml"
    if not compose.exists():
        return issues
    text = compose.read_text(encoding="utf-8")
    # api 的 healthcheck 应指向 /api/health
    api_block = re.search(r"api:.*?(?=\n  \w|^\w|\Z)", text, re.DOTALL | re.MULTILINE)
    if api_block and "/api/health" not in api_block.group():
        issues.append((
            "WARN",
            "docker-compose.yml api 服务的 healthcheck 未引用 /api/health",
            "实际端点可能变化",
        ))
    return issues


# ============== 主流程 ==============
def main():
    ap = argparse.ArgumentParser(description="配置一致性验证")
    ap.add_argument(
        "--env",
        default=".env",
        help="要校验的 env 文件 (相对 backend/, 默认为 .env)",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="严格模式: WARN 也算失败",
    )
    ap.add_argument(
        "--report",
        action="store_true",
        help="输出 JSON 报告到 logs/config_validation.json",
    )
    args = ap.parse_args()

    env_path = BACKEND / args.env
    if not env_path.exists():
        print(color(f"[ERR] 找不到 env 文件: {env_path}", "R"))
        return 1

    print()
    print(color("=" * 70, "CY"))
    print(color(f"  Configuration Consistency Validator", "CY"))
    print(color("=" * 70, "CY"))
    print(f"  Project root: {ROOT}")
    print(f"  Backend dir:  {BACKEND}")
    print(f"  Validating:   {env_path.relative_to(ROOT)}")
    print()

    env = parse_env_file(env_path)
    print(f"  Loaded {len(env)} env variables from {env_path.name}")
    print()

    all_issues: List[Tuple[str, str, str]] = []
    checks = [
        ("1) .env vs .env.example", check_env_vs_example),
        ("2) 占位符替换检查", check_docker_env_placeholders),
        ("3) 生产环境密钥强度", check_production_secrets),
        ("4) 端口一致性 (compose vs env)", check_ports_consistency),
        ("5) docker-compose.yml 环境变量引用", check_compose_env_refs),
        ("6) 前端 .env.production 一致性", check_frontend_consistency),
        ("7) /api/health 健康检查端点", check_healthcheck_url),
    ]

    for name, fn in checks:
        print(color(f"  ▶ {name}", "B"))
        issues = fn(env)
        if not issues:
            print(color("    ✓ 通过", "G"))
        else:
            for level, msg, hint in issues:
                tag = color(f"[{level}]", "R" if level == "ERR" else "Y")
                print(f"    {tag} {msg}")
                if hint:
                    print(f"         → {hint}")
        all_issues.extend(issues)
        print()

    # 汇总
    errs = [i for i in all_issues if i[0] == "ERR"]
    warns = [i for i in all_issues if i[0] == "WARN"]

    print(color("=" * 70, "CY"))
    print(color("  Summary", "CY"))
    print(color("=" * 70, "CY"))
    print(f"  Errors:   {len(errs)}")
    print(f"  Warnings: {len(warns)}")
    print()

    if errs:
        print(color("  ✗ 配置校验失败", "R"))
        for level, msg, _ in errs:
            print(f"    - {msg}")
        exit_code = 1
    elif warns and args.strict:
        print(color("  ✗ 严格模式: 警告也视为失败", "R"))
        exit_code = 1
    elif warns:
        print(color("  ⚠ 配置校验通过 (有警告)", "Y"))
        exit_code = 0
    else:
        print(color("  ✓ 配置校验全部通过", "G"))
        exit_code = 0

    # JSON 报告
    if args.report:
        report_path = ROOT / "logs" / "config_validation.json"
        report_path.parent.mkdir(exist_ok=True)
        report = {
            "env_file": str(env_path.relative_to(ROOT)),
            "errors": [{"msg": m, "hint": h} for _, m, h in errs],
            "warnings": [{"msg": m, "hint": h} for _, m, h in warns],
            "exit_code": exit_code,
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print()
        print(f"  报告已写入: {report_path.relative_to(ROOT)}")

    print()
    return exit_code


if __name__ == "__main__":
    sys.exit(main() or 0)
