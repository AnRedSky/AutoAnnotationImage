"""Pydantic Schemas: Auth"""
from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict
from typing import Optional, Literal


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    role: Optional[Literal["admin", "annotator", "viewer"]] = "annotator"


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
