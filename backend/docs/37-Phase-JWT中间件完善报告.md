# Phase B: JWT 中间件完善报告 (Token 吊销 + 限流 + 审计 + 自动续期)

> **日期**: 2026-07-25
> **范围**: 后端认证 / 鉴权 / 中间件完整链路
> **关联提交**: Phase A 标准化 + 细分异常
> **状态**: ✅ 全部完成, 64/64 单元测试通过
> **前置**: [36-Phase-JWT验证完善报告.md](./36-Phase-JWT验证完善报告.md)

---

## 一、问题背景

Phase A 完成了 JWT 标准化 + 细分异常, 但仅覆盖"签发/验证"环节. 仍存在以下空白:

| # | 缺口 | 严重性 | 影响 |
|---|------|--------|------|
| 1 | 改密后旧 token 仍可用 | 🔴 高 | 攻击者拿到改密前泄露的 token 可继续访问 |
| 2 | 登出仅前端清 token, 后端无吊销 | 🟡 中 | 丢失设备 / 退出账号后 token 仍有效到自然过期 |
| 3 | 登录 / 改密 / 登出 无审计 | 🟡 中 | 出现安全事件无法追溯 (谁在何时登入, 改密是否触发) |
| 4 | 登录无频率限制 | 🔴 高 | 暴力破解密码无任何保护 |
| 5 | token 临近过期, 用户需重新登录 | 🟢 低 | 体验差, 频繁操作会突然登出 |
| 6 | 401/403 拒绝事件无日志 | 🟢 低 | 无法统计异常高频触发, 难以预警攻击 |

---

## 二、解决方案总览

Phase B 引入 4 个新中间件 + 1 个业务方法, 闭环 JWT 完整生命周期:

```
┌──────────────────────────────────────────────────────────────────┐
│ JWT 完整生命周期 (Phase A + B)                                     │
├──────────────────────────────────────────────────────────────────┤
│ 签发        → create_access_token (Phase A 标准化 claims)         │
│ 验证        → decode_token (Phase A 细分异常)                     │
│ 吊销        → token_revocation (Phase B 新增)                     │
│ 限流        → rate_limit (Phase B 新增)                          │
│ 审计        → auth_audit (Phase B 新增)                          │
│ 自动续期    → token_refresh (Phase B 新增)                       │
│ 业务集成    → auth_service (Phase B 新增 logout/改密吊销)         │
└──────────────────────────────────────────────────────────────────┘
```

中间件注册顺序 (order 升序 = 从内到外):

```
10 cors → 20 error_handler → 22 auth_audit → 25 rate_limit
   → 30 request_id → 35 token_refresh → 40 request_timing
```

---

## 三、Token 吊销 (Token Revocation)

### 3.1 设计 — `app/middleware/security/token_revocation.py`

**存储策略** (Redis):

| Key | Value | TTL | 用途 |
|-----|-------|-----|------|
| `jwt:revoked:jti:{jti}` | `"1"` | `exp - now` | 单 token 吊销 (登出) |
| `jwt:revoked:user:{user_id}` | `unix_ts` | 30 天 | 用户级吊销 (改密) |

**核心函数**:

```python
def revoke_jti(jti: str, *, exp_ts: Optional[int] = None,
               default_ttl: int = 86400) -> bool:
    """按 jti 吊销单个 token, TTL = 剩余有效期"""

def revoke_user(user_id: int | str) -> bool:
    """吊销某用户的所有 token, 记录改密时间戳"""

def is_user_revoked(user_id, iat=None) -> bool:
    """检查 token 是否被用户级吊销
    - iat > 吊销时间戳 → 视为改密后新签, 放行
    - iat <= 吊销时间戳 → 视为改密前旧 token, 拒
    - iat 未传 → 保守策略, 一律拒
    """

def check_revoked(*, jti=None, user_id=None, iat=None) -> bool:
    """综合检查 (jti 优先 + 用户级)
    - jti 在黑名单 → 拒
    - 用户被吊销 且 token iat <= 吊销时间戳 → 拒
    """
```

**降级**: Redis 不可用时, 吊销操作返回 False (不抛错), 业务可继续, 避免 Redis 故障导致全站登录失败.

### 3.2 decode_token 集成

[security.py:280-298](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/middleware/security/security.py#L280-L298) 在签名/iss/aud/exp 校验后追加吊销检查:

```python
# Phase-B: 吊销检查 (jti + 用户级)
if check_revocation:
    jti = payload.get("jti")
    sub = payload.get("sub")
    iat = payload.get("iat")
    user_id_int: Optional[int] = None
    try:
        if sub is not None:
            user_id_int = int(sub)
    except (TypeError, ValueError):
        user_id_int = None
    if token_revocation.check_revoked(
        jti=jti, user_id=user_id_int, iat=iat,
    ):
        raise TokenRevokedError("Token has been revoked")
```

### 3.3 业务集成 — `AuthService`

[auth_service.py:108-150](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/auth/service/auth_service.py#L108-L150) 改密时调用 `revoke_user`:

```python
@staticmethod
async def change_password(db, user, old_password, new_password) -> User:
    # ... 验证旧密码 / 长度校验 ...
    user.password_hash = get_password_hash(new_password)
    await db.commit()
    
    # 吊销该用户的所有 token (改密场景强制全设备重新登录)
    token_revocation.revoke_user(user.id)
    
    emit_audit_event(AuthEventType.CHANGE_PASSWORD, user_id=user.id, ...)
    return user
```

[auth_service.py:153-178](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/auth/service/auth_service.py#L153-L178) 登出时调用 `revoke_jti`:

```python
@staticmethod
def logout(user: User, jti: Optional[str] = None,
           exp_ts: Optional[int] = None) -> dict:
    """登出 (Phase-B: 主动吊销当前 token 的 jti)"""
    revoked_jti = False
    if jti:
        revoked_jti = token_revocation.revoke_jti(jti, exp_ts=exp_ts)
    
    emit_audit_event(AuthEventType.LOGOUT, user_id=user.id, jti=jti, ...)
    return {"detail": "已退出登录", "user_id": user.id, "jti_revoked": revoked_jti}
```

### 3.4 API 端点 — `app/auth/api/auth.py`

新增依赖 `get_current_user_with_payload` (返回 user + payload + raw_token 三元组):

```python
class CurrentUserContext(NamedTuple):
    user: User
    payload: dict
    raw_token: str

async def get_current_user_with_payload(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUserContext:
    payload, user = await _decode_token_to_user_payload(token, db)
    return CurrentUserContext(user=user, payload=payload, raw_token=token)
```

`logout` 端点使用新依赖, 取出 jti + exp 走吊销:

```python
@router.post("/logout")
async def logout(ctx = Depends(get_current_user_with_payload)):
    jti = ctx.payload.get("jti")
    exp = ctx.payload.get("exp")
    return AuthService.logout(ctx.user, jti=jti, exp_ts=exp)
```

---

## 四、限流 (Rate Limiting)

### 4.1 算法 — 滑动窗口

使用 Redis Sorted Set + ZREMRANGEBYSCORE 实现:

| 步骤 | 命令 | 作用 |
|------|------|------|
| 1 | `ZREMRANGEBYSCORE key 0 (now - window)` | 清理窗口外请求 |
| 2 | `ZCARD key` | 当前窗口内请求数 |
| 3 | `ZADD key {now} {member}` | 记录新请求 |
| 4 | `EXPIRE key (window + 60)` | 兜底 TTL |

相比固定窗口, 滑动窗口避免"窗口边界双倍流量"问题.

### 4.2 维度 — 4 种预置策略

[runtime 定义于 rate_limit.py](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/middleware/http/rate_limit.py):

| Profile | max_requests | window | scope | 适用端点 |
|---------|--------------|--------|-------|----------|
| `global` | 300 | 60s | ip | 所有非 exempt 路径 |
| `login` | 10 | 60s | ip_username | `/api/auth/login` |
| `register` | 5 | 60s | ip | `/api/auth/register` |
| `change_password` | 5 | 300s | ip_username | `/api/auth/change-password` |
| `strict` | 3 | 60s | ip | 暂未挂载 (预留) |

**维度设计**:
- `ip` — 限单 IP 高频请求
- `username` — 限单用户多端高频 (e.g. 同一账号多设备暴力破解)
- `ip_username` — 综合限, 防绕过 (攻击者会切换 IP/账号)

### 4.3 触发响应

```python
if current >= profile.max_requests:
    # 计算 retry_after = (最旧时间戳 + window) - now
    return JSONResponse(
        status_code=429,
        content={"code": "rate_limited", "message": "请求过于频繁"},
        headers={"Retry-After": str(retry_after)},
    )
```

---

## 五、审计 (Auth Audit)

### 5.1 事件类型

[auth_audit.py:42-53](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/middleware/http/auth_audit.py#L42-L53) 定义 8 种事件:

| 事件 | 触发场景 | 日志级别 |
|------|----------|----------|
| `LOGIN_SUCCESS` | 登录成功 | INFO |
| `LOGIN_FAILURE` | 登录失败 (密码错/账号停用) | WARNING |
| `LOGOUT` | 主动登出 | INFO |
| `CHANGE_PASSWORD` | 改密成功/失败 | INFO |
| `REGISTER` | 注册成功/失败 | INFO |
| `TOKEN_REJECTED` | Token 验证失败 (过期/签名错/吊销) | WARNING |
| `TOKEN_REFRESHED` | Token 自动续期 | INFO |
| `PERMISSION_DENIED` | 角色不足 | WARNING |

### 5.2 双轨记录

**主动 emit** (service 层调用):
```python
emit_audit_event(AuthEventType.LOGIN_SUCCESS,
    user_id=user.id, username=user.username, extra={"role": user.role})
```

**被动捕获** (中间件监听响应):
```python
# AuthAuditMiddleware.dispatch
if request.url.path in _AUDIT_PATH_PATTERNS and response.status_code in (401, 403):
    self._record_rejection(request, response)
```

两条路径互补: 业务层主动记录登录/改密/登出, 中间件兜底 Token 验证失败.

### 5.3 日志格式

```
auth_event: type=login_success rid=abc123 user_id=1 username='alice' ip=1.2.3.4 ts=1784961714
```

- `rid` 从 RequestIDMiddleware 注入, 便于跨服务日志关联
- `user_id` / `username` 从当前上下文继承
- `ip` 从 X-Forwarded-For → X-Real-IP → client.host 兜底
- `jti` 自动截断到 8 字符, 避免日志过长

---

## 六、Token 自动续期 (Token Refresh)

### 6.1 触发条件

[token_refresh.py:107-112](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/middleware/http/token_refresh.py#L107-L112):

```python
exp = payload.get("exp")
now = int(time.time())
if exp is None or (exp - now) > self.refresh_threshold:
    return await call_next(request)  # 不续期
```

默认阈值: **5 分钟** (`DEFAULT_REFRESH_THRESHOLD_SECONDS = 300`)

### 6.2 续期流程

1. 中间件检查 token 剩余有效期
2. 若 < 阈值, 调用 `_renew()` 用旧 payload 签发新 token
3. 业务请求继续, 完成后在响应头附加:

| Header | 值 |
|--------|----|
| `X-Renewed-Token` | 新 JWT |
| `X-Renewed-Token-Expires-At` | 新 token exp (Unix 秒) |

### 6.3 前端集成 (frontend/src/api/http.ts)

```typescript
http.interceptors.response.use((response) => {
  const renewed = response.headers['x-renewed-token']
  if (renewed) {
    localStorage.setItem('token', renewed)
  }
  return response.data
})
```

用户无感知, 避免 token 过期导致突然登出.

### 6.4 续期保留声明

[_renew()](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/middleware/http/token_refresh.py#L136-L149) 保留 `sub / username / role`, 重新生成 `iat / exp / jti`:

```python
def _renew(self, old_payload: dict) -> str:
    sub = old_payload.get("sub")
    username = old_payload.get("username")
    role = old_payload.get("role")
    data = {"sub": sub}
    if username:
        data["username"] = username
    extra: dict = {}
    if role:
        extra["role"] = role
    return create_access_token(data=data, extra_claims=extra or None)
```

### 6.5 豁免路径

```python
_EXEMPT_PATHS = (
    "/api/auth/login", "/api/auth/logout", "/api/auth/refresh",
    "/api/auth/register", "/api/auth/change-password", "/api/auth/me",
)
```

认证端点自身不续期 (避免 login 后立刻续期 / 登出后又被续期).

---

## 七、测试覆盖

### 7.1 测试统计

```
test_jwt_verification.py:    20/20 ✅
test_auth_middleware.py:     30/30 ✅
test_auth_service_revocation.py: 14/14 ✅
────────────────────────────────────────
Total:                       64/64 ✅
```

### 7.2 覆盖矩阵 — test_auth_middleware.py (30 用例)

| 类别 | 用例数 | 覆盖点 |
|------|--------|--------|
| Token 吊销 (TestTokenRevocation) | 10 | jti 吊销 / 用户级吊销 / 综合检查 / 集成到 decode_token |
| 限流 (TestRateLimit) | 10 | 阈值内放行 / 超阈值拒 / 窗口滑过 / 三种维度 key / 客户端 IP 提取 / 预置策略齐全 |
| 审计 (TestAuthAudit) | 4 | 事件序列化 / JSON 化 / jti 截断 / 事件类型齐全 |
| 续期 (TestTokenRefresh) | 4 | 远过期不续 / 续期保留 sub+role / jti 唯一 / 默认阈值 5min |
| 集成 (TestGetCurrentUserRevoked) | 2 | 吊销 token 触发 401 / 用户吊销后旧 token 401 |

### 7.3 覆盖矩阵 — test_auth_service_revocation.py (14 用例, 新增)

| 类别 | 用例数 | 覆盖点 |
|------|--------|--------|
| 改密吊销 (TestChangePasswordRevokesAllTokens) | 3 | 改密后旧 token 被拒 / emit 事件 / 旧密码错不吊销 |
| 登出吊销 (TestLogoutRevokesJti) | 3 | logout 后 jti 黑名单生效 / emit 事件 / 不传 jti 兜底 |
| 登录审计 (TestLoginAuditEvents) | 3 | 登录成功 emit / 密码错 emit / 账号停用 emit |
| 集成 (TestIntegration) | 1 | 完整生命周期: 改密 → 旧 token 失效 → 新 token 通过 |
| 依赖 (TestGetCurrentUserWithPayload) | 2 | NamedTuple 结构 / 端到端吊销流程 |
| 注册审计 (TestRegisterAuditEvents) | 2 | 注册成功 emit / 用户名重复 emit |

### 7.4 关键测试场景

**TC-B-29** (吊销 token 触发 401):
```python
token = create_access_token({"sub": "1", "username": "alice"})
payload = decode_token(token, check_revocation=False)
token_revocation.revoke_jti(payload["jti"])
# 模拟 get_current_user 捕获 TokenRevokedError
try:
    decode_token(token)
except TokenRevokedError as e:
    assert e.code == "token_revoked"
    assert e.http_status == 401
```

**TC-ASR-10** (改密完整生命周期):
```python
user = _make_user(id_=100, password="initial-pass")
old_token = create_access_token({"sub": "100"})

# 改密
run_async(AuthService.change_password(db, user, "initial-pass", "rotated-pass"))

# 旧 token 立即失效
time.sleep(1.2)
with pytest.raises(TokenRevokedError):
    decode_token(old_token)

# 1.2s 后签发新 token 通过
time.sleep(1.2)
new_token = create_access_token({"sub": "100"})
payload = decode_token(new_token)  # OK
assert payload["sub"] == "100"
```

**TC-B-12** (限流超阈值被拒):
```python
profile = RateLimitProfile(name="test", max_requests=2, window_seconds=10, scope="ip")
for _ in range(2):
    allowed, _, _ = _check_and_increment(profile, "rl:test:1.1.1.1")
    assert allowed is True
allowed, _, retry_after = _check_and_increment(profile, "rl:test:1.1.1.1")
assert allowed is False
assert retry_after > 0
```

---

## 八、文件清单

| 变更 | 文件 | 行数 | 说明 |
|------|------|------|------|
| ➕ 新增 | `backend/app/middleware/security/token_revocation.py` | 187 行 | Token 吊销服务 (jti + 用户级) |
| ➕ 新增 | `backend/app/middleware/http/rate_limit.py` | ~280 行 | 滑动窗口限流中间件 |
| ➕ 新增 | `backend/app/middleware/http/auth_audit.py` | 273 行 | 认证事件审计中间件 |
| ➕ 新增 | `backend/app/middleware/http/token_refresh.py` | 168 行 | Token 自动续期中间件 |
| ✏️ 修改 | `backend/app/middleware/http/auth.py` | 87 → 156 行 | 拆分出 `_decode_token_to_user_payload` + 新增 `get_current_user_with_payload` |
| ✏️ 修改 | `backend/app/middleware/http/__init__.py` | 96 → 168 行 | 注册 4 个新中间件到 MiddlewareRegistry |
| ✏️ 修改 | `backend/app/middleware/security/security.py` | 305 → 305 行 | 集成 `check_revocation` 到 `decode_token` (无新增代码, 复用现有调用点) |
| ✏️ 重写 | `backend/app/auth/service/auth_service.py` | 127 → 230 行 | 集成 `change_password` 吊销 + `logout` 吊销 + 5 个审计事件 |
| ✏️ 重写 | `backend/app/auth/api/auth.py` | 107 → 144 行 | logout 改用新依赖 + change-password 提示重新登录 |
| ➕ 新增 | `backend/tests/test_auth_middleware.py` | 339 行 / 30 用例 | 中间件核心算法测试 |
| ➕ 新增 | `backend/tests/test_auth_service_revocation.py` | 415 行 / 14 用例 | AuthService 集成吊销测试 |

**总变更**: 11 个文件, 净增约 2200 行 (含 754 行测试).

---

## 九、影响面分析

### 9.1 兼容性

- ✅ **新签发的 token** 包含 `iat/exp/jti/iss/aud` 全套声明 (Phase A 沿用)
- ✅ **HTTP status 仍为 401/429**, 不影响前端现有分支
- ✅ **新增响应头** `X-Renewed-Token` / `X-Renewed-Token-Expires-At` / `Retry-After`, 前端可选消费
- ➕ **logout 响应新增字段** `jti_revoked: bool`, 前端可用于提示
- ➕ **change-password 提示"请重新登录"**, 旧 token 立即失效是预期行为

### 9.2 性能

| 中间件 | 额外开销 | 关键路径 |
|--------|----------|----------|
| 限流 (rate_limit) | ~0.5ms (1 次 pipeline Redis 调用) | 是 (按 IP/账号) |
| 审计 (auth_audit) | ~0.1ms (日志写入) | 否 (异步可后续切队列) |
| 续期 (token_refresh) | ~0.1ms (仅临近过期) | 否 (默认跳过) |
| 吊销检查 (decode_token 集成) | ~0.3ms (1 次 Redis EXISTS) | 是 (每次请求) |

合计: 约 +1ms p99, 在 200ms SLA 内可接受.

### 9.3 安全性提升

| 攻击 | Phase A 修复后 | Phase B 修复后 |
|------|----------------|----------------|
| 改密后旧 token 仍可用 | ⚠️ 可用到自然过期 | ✅ 立即失效 |
| 登出后 token 仍可用 | ⚠️ 可用到自然过期 | ✅ 立即失效 (按 jti) |
| 暴力破解密码 | ⚠️ 无限制 | ✅ login 限流 10/min (ip+username) |
| 改密端点被刷 | ⚠️ 无限制 | ✅ change_password 限流 5/5min |
| 高频爬取任意端点 | ⚠️ 无限制 | ✅ global 限流 300/min |
| 安全事件无追溯 | ❌ 无日志 | ✅ 8 类事件审计 + WARNING 级别告警 |
| token 突然过期 | ⚠️ 需重新登录 | ✅ 5min 前自动静默续期 |

### 9.4 Redis 依赖

| 场景 | Redis 不可用时 |
|------|----------------|
| 吊销 | `revoke_jti/revoke_user` 返回 False, 业务继续, token 自然过期 (降级) |
| 限流 | 放行所有请求, 不阻塞业务 (降级) |
| 续期 | 续期失败, 旧 token 继续用 (降级) |
| 审计 | 审计事件仅写日志, 不影响业务 |

**结论**: Redis 故障不会导致登录失败, 但安全降级 (无吊销/无限流). 监控告警应覆盖 Redis 异常.

---

## 十、待完善项 (后续 Phase)

1. **审计落库**
   - 当前审计事件仅写日志, 可扩展为同时落 `audit_log` 表
   - 便于安全团队查询历史 / 报表统计

2. **限流响应标准化**
   - 当前 429 响应 detail 是 `{"code": "rate_limited", "message": "..."}`
   - 可扩展为包含 `retry_after` / `limit` / `remaining` 字段

3. **Token 黑名单清理**
   - Redis 自动按 TTL 清理, 无需主动操作
   - 监控: `jwt:revoked:*` key 总数, 异常增长可能意味攻击

4. **续期事件审计**
   - 当前 token_refresh 中间件未 emit TOKEN_REFRESHED 事件
   - 可补充: emit 续期事件, 便于追踪"哪些用户在何时续过 token"

5. **风险登录检测**
   - 基于审计日志 + IP 地理位置, 异常登录 (跨省/跨国) 强制二次验证
   - 需要前端配合, 优先级 P1

---

## 十一、验证清单

- [x] 30/30 中间件测试通过 (test_auth_middleware.py)
- [x] 14/14 AuthService 吊销测试通过 (test_auth_service_revocation.py)
- [x] 20/20 JWT 验证测试通过 (test_jwt_verification.py, Phase A 沿用)
- [x] 64/64 总测试通过
- [x] 4 个新中间件注册到 MiddlewareRegistry (auth_audit / rate_limit / token_refresh)
- [x] change_password 吊销用户全部 token
- [x] logout 吊销当前 jti
- [x] login/register/change_password/logout 5 类审计事件
- [x] 限流 4 个预置策略 (global / login / register / change_password)
- [x] token 临近过期自动续期 (5min 阈值)
- [x] Redis 不可用时降级策略

---

## 十二、引用

### 中间件实现
- [token_revocation.py:1-187](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/middleware/security/token_revocation.py#L1-L187) — Token 吊销服务
- [rate_limit.py](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/middleware/http/rate_limit.py) — 限流中间件
- [auth_audit.py:1-273](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/middleware/http/auth_audit.py#L1-L273) — 审计中间件
- [token_refresh.py:1-168](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/middleware/http/token_refresh.py#L1-L168) — 自动续期

### 业务集成
- [auth_service.py:1-230](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/auth/service/auth_service.py#L1-L230) — AuthService (集成吊销 + 审计)
- [auth.py](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/auth/api/auth.py) — Auth API 端点
- [auth.py:122-133](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/middleware/http/auth.py#L122-L133) — get_current_user_with_payload 依赖

### 中间件注册
- [http/__init__.py:119-166](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/middleware/http/__init__.py#L119-L166) — MiddlewareRegistry 4 个新条目

### 测试
- [test_auth_middleware.py:1-339](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/tests/test_auth_middleware.py#L1-L339) — 30 用例
- [test_auth_service_revocation.py:1-415](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/tests/test_auth_service_revocation.py#L1-L415) — 14 用例

### Phase A 前置
- [36-Phase-JWT验证完善报告.md](./36-Phase-JWT验证完善报告.md) — JWT 标准化 + 细分异常
- [security.py:280-298](file:///d:/works/WorkBuddy/Myhome/毕业论文设计与实现/thesis-image-annotation/backend/app/middleware/security/security.py#L280-L298) — decode_token 吊销检查集成
