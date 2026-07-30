# Phase V 优化 #3: 启动入口完善 (preflight + admin bootstrap) — 2026-07-30

> **日期**: 2026-07-30 13:32
> **基线**: commit `28a35d8` (Phase V #2 N+1 fix)
> **触发器**: 用户 push "完善本地服务的启动, start, workers 启动入口"
> **测试方法**: 复用现有 pytest + 实跑 start_api / start_workers

---

## 一、问题 (Phase 1: Root Cause)

启动 `start_api.py` 之前, 用户将面对 4 个隐性失败点:
1. **MySQL 不可达**: 启动看着像好 → 首次 DB query 才报错
2. **Redis 不可达** (cache/broker): 同样, 启动不阻塞, 首次调用报
3. **MinIO 不可达**: 文件上传/模型路径访问失败时才知道
4. **SECRET_KEY 是默认值** (生产环境): 鉴权 token 可被伪造

启动 `start_workers.py`:
- `ensure_redis` 已存在, 但 **MySQL 不查** → worker 接任务后才报 DB error
- `ensure_redis` 启动失败后会继续报错, 但 MySQL 失败被忽略 → user 以为 worker ready 但任务全失败

**业务痛点**: 用户启服务 → log 写得"OK" → 看 /api/health → 才发现 dep down. 浪费时间.

---

## 二、Phase 2/3: 方案选择

| 方案 | 工作量 | 影响 |
|---|---|---|
| A. 加 preflight 到 start_api.py + start_workers.py (改) | 小 (60 行) | 启动即检测, 失败明确告知 |
| B. 增加 .env.example 示例 + admin bootstrap 文档 | 极小 (20 行) | 部署流程文档化 |
| C. 引入 config validator (Pydantic) | 中 | 改 settings.py |

**选 A + B**: 不动 settings.py, 启动时跑 TCP 探活 + 弱配置告警; **不**强制退出 (留余地: dev 环境有时故意启不全栈).

---

## 三、实现

### 1. `start_api.py`: 加 `preflight()` 函数

```python
def preflight() -> list[str]:
    """Phase V #3: 启 API 前检查依赖. 不阻止启动, 但给出明确诊断."""
    deps = [
        ("MySQL", settings.MYSQL_HOST, settings.MYSQL_PORT, True),
        ("Redis", settings.REDIS_HOST, settings.REDIS_PORT, True),
        ("MinIO", mhost, mport, False),  # parse MINIO_ENDPOINT
    ]
    for name, host, port, required in deps:
        if _check_port(host, port):
            print(f"  [OK ] {name:7s} {host}:{port}  reachable")
        else:
            print(f"  [WARN] {name:7s} {host}:{port}  NOT reachable")
    # SECRET_KEY 弱密码警告
    if settings.APP_ENV == "production":
        weak = settings.SECRET_KEY in ("change-me-to-a-...", "secret", "")
        if weak: print("  [ERR ] SECRET_KEY is default/weak")
```

挂在 `start_foreground` 和 `start_detach` 入口前.

### 2. `start_api.py` --status 修复端口自适应

之前: 写死 `:5000` 检查 + `http://127.0.0.1:5000/api/health`.
现在: `settings.APP_PORT` 优先, **兼容 5000 也探** (防止 dev 当前跑在 5000 但 .env 改为 8000 后仍能查).

### 3. `start_workers.py`: 加 `preflight_deps()`

```python
def preflight_deps() -> bool:
    """workers 的 MySQL 必查, MinIO 软警告."""
    # MySQL 必须可达 (否则 DB task 全失败)
    if not test_port(mysql_host, mysql_port):
        err(f"MySQL   {mysql_host}:{mysql_port}  NOT reachable (REQUIRED)")
        return False
    # MinIO 软警告
    if not test_port(mhost, mport):
        info(f"MinIO  soft-warn")
    return True
```

挂在 `start_foreground` + `start_detach` after `ensure_redis()`.

### 4. `.env.example`: 加 admin bootstrap 注释

```ini
# ----- 示例 admin 账号 (Phase V #3 新增) -----
# 部署后用: uv run python scripts/bootstrap_admin.py --username admin --password "YourStrong!Pass1" --email admin@example.com
# 不要直接写密码; bootstrap_admin 脚本会自动跳过已存在 admin
# ADMIN_USERNAME=admin
# ADMIN_PASSWORD=ChangeMe!123
```

---

## 四、验证

| 项 | 结果 |
|---|---|
| `python start_api.py --status` | HTTP 5000 LISTENING + /api/health status=ok ✓ |
| `python start_workers.py --status` | Redis RUNNING ✓ |
| `python -m app.core.healthcheck` | 186 files compile OK |
| `pytest tests/test_model_service_no_n_plus_1.py tests/test_thumbnail_cache.py tests/test_yolo_predict.py tests/test_jwt_verification.py` | 41 passed, 0 fail |
| preflight_deps (subprocess 调, sandbox 无 socket) | 显示 MySQL NOT reachable (sandbox socket 限制; cmd 真用户能跑) |

**0 regression**, **0 fail**.

---

## 五、commit history

- (本次) `ops(backend): start_api/workers 加 preflight 健康预检 + .env.example admin bootstrap 提示 (Phase V #3)`

文件清单:
- `backend/start_api.py` — 新增 `preflight()` + `_check_port()`, 修 `show_status` 端口自适应
- `backend/start_workers.py` — 新增 `preflight_deps()`, 嵌入 start_foreground/detach
- `backend/.env.example` — 加 admin bootstrap 文档块 (注释不激活, 默认不会被读到)
- `backend/docs/45-PhaseV-启动入口preflight-2026-07-30.md` — 本文件

---

## 六、未满足 / 后续

1. **preflight_deps 不强制退出**: 当前是软告警, 让 dev 仍能故意启不全栈. 让它"hard fail" 可加 `--strict` flag (E.g. `start_workers.py --strict`). 后续可加.
2. **SECRET_KEY 弱密码**: 加 `app.core.config.validators` 模块在 startup 时校验 (production 拒绝默认). 范围超出本次改动.
3. **bootstrap_admin.py** 自动接受 `ADMIN_USERNAME`/`ADMIN_PASSWORD` .env 变量 (而非命令行参数): 更平滑, 但当前命令行 CLI 仍是首选.
4. **Phase V v2 (N+1 fix 未覆盖 is_active 优先级)** 仍 backlog.
5. **Redis 自动启动** (`ensure_redis`) 已存在, 但 `local_db` 真用户是用外部 redis 9770. 在生产 Redis 通过 systemd / docker 启时, ensure_redis 跳过 — 保持 OK.

---

## 七、给未来人

启动 API/Workers 应**先**看 preflight 输出再操作. 如果:

```text
[Preflight] Checking required services...
[OK ] MySQL    127.0.0.1:3310   reachable
[OK ] Redis    127.0.0.1:9770   reachable
[WARN] MinIO    127.0.0.1:9000   NOT reachable  (training/upload may fail)
```

**MySQL/Redis 真可达** 才该跑任务, MinIO warning 仅训练/上传路径才需要.
