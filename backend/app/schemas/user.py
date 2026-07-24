"""Pydantic Schemas: User (v3.0.0 Phase 4 新增)

把分散在 auth.py / user.py / API 内联的 user schema 集中, 便于复用.
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field


class UserBase(BaseModel):
    """用户基础字段"""
    username: str = Field(..., min_length=3, max_length=64)
    email: Optional[str] = None
    role: str = Field(default="annotator", description="admin / annotator / viewer")


class UserOut(UserBase):
    """用户输出 (含 ID / 状态 / 时间)"""
    id: int
    is_active: bool = True
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class UserListOut(BaseModel):
    """用户列表响应 (含分页)"""
    total: int
    items: List[UserOut]


class UserUpdateRequest(BaseModel):
    """修改用户信息 (admin 可改任意, 用户本人可改 email)"""
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserChangePasswordRequest(BaseModel):
    """改密请求"""
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=128)


__all__ = [
    "UserBase",
    "UserOut",
    "UserListOut",
    "UserUpdateRequest",
    "UserChangePasswordRequest",
]
