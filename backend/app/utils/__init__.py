"""
Utils Package — 纯函数工具 (横切复用)
===================================

包含:
- 分页 (pagination)
- SSE 流 (sse)
- 时间 (datetime)
- 文件 (file_utils)
- 字符串 (string_utils)
- 哈希 (hash_utils)
- 图像 (image_utils)
- 异步桥 (async_helpers)
- 校验器 (validators)

依赖方向: utils 必须是纯函数, 不依赖任何业务层, 不依赖 common/core/database/middleware.
"""
