"""
Common Package — 业务可复用组件 (横切关注点)
=========================================

包含:
- 业务枚举 (enums)
- 业务异常 (exceptions)
- 常量 (constants)
- 共享类型 (types)
- 抽象接口 (interfaces)
- 事件总线 (events)
- ORM 基类扩展 (base_model)

依赖方向: common 不依赖任何业务层, 仅依赖标准库和 pydantic.
"""
