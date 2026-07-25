"""
Admin ORM Models — app/admin/model/
===================================

**v3.0.0 Stage 2.3 新增**: admin 应用的 ORM 模型集合.
当前包含: User
未来可能新增: Role, AuditLog, Permission

**依赖**: app.common.base_model.Base (跨应用共享)
"""
from app.admin.model.user import User

__all__ = ["User"]
