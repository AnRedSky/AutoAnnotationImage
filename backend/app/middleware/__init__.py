"""
Middleware Package — 中间件 (横切复用)
=====================================

包含:
- HTTP 中间件 (CORS, RequestID, Logging, Timing, ErrorHandler, Auth)
- 安全 (JWT, Password, Permissions)
- 可观测性 (Metrics, Tracing, Audit)

依赖方向: middleware 可依赖 common/core/utils, 不依赖 app/*.
"""
