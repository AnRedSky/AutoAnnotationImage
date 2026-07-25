"""
SQLAlchemy ORM Base Class (Common Layer)
========================================

所有 ORM 模型继承此 Base. 提供统一的 DeclarativeBase, 由各 app/*/model/ 使用.

**v3.0.0 Stage 2.3 迁移**: 从 app/model/base.py 迁入 app/common/base_model.py
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy ORM 基类

    所有 app/*/model/ 下的 ORM 模型继承此类, 确保它们注册在同一个
    SQLAlchemy MetaData 中, 跨模型的关系 (relationship) 才能正常解析.
    """
    pass


__all__ = ["Base"]
