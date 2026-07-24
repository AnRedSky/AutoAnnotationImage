"""
SQLAlchemy ORM Base Class (Model Layer)
=======================================

所有 ORM 模型继承此 Base. 在 v3.0 重命名 models/ -> model/ 时, 此处作为
Base 类的归属地 (与具体业务 ORM 模型并列, 方便 model 目录自治).

v3.0.0 新增 (Phase 1.9): 从 app.database.Base 提取
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy ORM 基类"""
    pass
