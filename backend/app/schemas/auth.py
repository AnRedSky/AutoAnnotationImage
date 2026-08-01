"""Pydantic Schemas: Auth"""
from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict, Field
from typing import Optional, Literal


class RegisterRequest(BaseModel):
    """注册请求 (公开注册, 任何人可自主注册)
    - 密码: 至少 8 位 (Pydantic 自动校验, 失败 422)
    - 角色: 已废弃, 公开注册固定为 annotator (后端强制, 请求中的 role 字段被忽略)
    """
    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    password: str = Field(..., min_length=8, max_length=128, description="密码 (至少 8 位)")
    email: Optional[EmailStr] = Field(default=None, description="邮箱")
    role: Optional[Literal["admin", "annotator", "viewer"]] = Field(
        default="annotator", description="已废弃: 公开注册固定 annotator, 此字段被忽略"
    )


class ChangePasswordRequest(BaseModel):
    """修改密码请求 (v3.0.0 新增 API)"""
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128, description="新密码至少 8 位")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int


class UserOut(BaseModel):
    """当前用户信息（/auth/me）"""
    id: int
    username: str
    email: Optional[str] = None
    role: str
    is_active: bool
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
