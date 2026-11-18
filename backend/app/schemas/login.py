from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


# ============ 登录 / JWT ============
class CredentialsSchema(BaseModel):
    email: str = Field(..., description='邮箱', example='you@example.com')
    password: str = Field(..., description='密码', example='123456')


class JWTPayload(BaseModel):
    user_id: str
    exp: datetime


class JWTOut(BaseModel):
    """登录返回体：access + refresh 都走响应体（前端存 localStorage），不再用 httpOnly cookie。

    去掉 cookie 是为了让 CORS 能朴素地 allow_origins=['*']——带 cookie 就得 allow_credentials，
    而 ['*'] + credentials 是浏览器禁止的组合，只能回显 origin 兜圈子，纯属增加复杂度。
    """

    access_token: str
    refresh_token: str
    user_id: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., description='登录时下发的 refresh token')


# ============ 注册 / 找回密码 ============
class VerifyCodeRequest(BaseModel):
    email: str = Field(..., description='用户邮箱', example='you@example.com')


class RegisterRequest(BaseModel):
    user_name: str = Field(..., description='用户名称', example='admin')
    password: str = Field(..., description='密码', example='your-password')
    email: str = Field(..., description='用户邮箱', example='you@example.com')
    verification_code: str = Field(..., description='验证码', example='123456')


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., description='重置令牌')
    new_password: str = Field(..., description='新密码')


# ============ 用户资料 CRUD（原 schemas/admin.py 折入） ============
class UserCreate(BaseModel):
    user_id: Optional[str] = Field(default=None, example='user123')
    user_name: str = Field(example='admin')
    password: Optional[str] = Field(default=None, example='123456ai')
    email: Optional[EmailStr] = Field(default=None, example='you@example.com')
    is_active: Optional[bool] = Field(default=True)


class UserUpdate(BaseModel):
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None


class UpdatePassword(BaseModel):
    old_password: str = Field(..., example='123456')
    new_password: str = Field(..., example='123456new')
