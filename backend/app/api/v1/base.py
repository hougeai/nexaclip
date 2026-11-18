import jwt
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from controllers import user_controller
from core.config import settings
from core.dependency import DependAuth
from core.security import create_token, get_password_hash, verify_password
from core.verifycode import CodeManager
from models.user import User
from schemas.base import Fail, Success
from schemas.login import (
    CredentialsSchema,
    JWTOut,
    JWTPayload,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    UpdatePassword,
    VerifyCodeRequest,
)

router = APIRouter()

code_manager = CodeManager()


def _make_tokens(user) -> JWTOut:
    """签发 access + refresh 双 token，全部走响应体（无 cookie）。"""
    access_expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_expire = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    return JWTOut(
        access_token=create_token(data=JWTPayload(user_id=user.user_id, exp=access_expire)),
        refresh_token=create_token(data=JWTPayload(user_id=user.user_id, exp=refresh_expire)),
        user_id=user.user_id,
    )


@router.post('/access_token', summary='登录获取 token')
async def access_token(credentials: CredentialsSchema):
    user = await user_controller.authenticate(credentials)
    await user_controller.update_last_login(user.user_id)
    return Success(data=_make_tokens(user).model_dump())


@router.post('/refresh_token', summary='刷新 token')
async def refresh_token(req: RefreshRequest):
    try:
        payload = jwt.decode(
            req.refresh_token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={'verify_exp': True},
        )
        user_id = payload.get('user_id', '')
        if not user_id:
            return Fail(msg='无效的 refresh token')
        user = await user_controller.get_by_user_id(user_id)
        if not user:
            return Fail(msg='用户不存在')
        return Success(data=_make_tokens(user).model_dump())  # 轮换：返回新的 access + refresh
    except jwt.ExpiredSignatureError:
        return Fail(msg='refresh token 已过期')
    except jwt.InvalidTokenError:
        return Fail(msg='无效的 refresh token')
    except Exception as e:
        return Fail(msg=f'token 验证失败: {str(e)}')


# ============ 注册 / 找回密码（邮箱验证码） ============


@router.post('/verifycode', summary='发送邮箱验证码')
async def send_verify_code(req: VerifyCodeRequest):
    if await User.filter(email=req.email, is_del=False).exists():
        return Fail(msg='邮箱已注册')
    success, msg = await code_manager.generate_code(req.email)
    return Fail(msg=msg) if not success else Success(msg=msg)


@router.post('/register', summary='用户注册（验证码校验；同邮箱已注销则恢复账号）')
async def register(req: RegisterRequest):
    success, msg = await code_manager.verify_code(req.email, req.verification_code)
    if not success:
        return Fail(msg=msg)
    old = await User.filter(email=req.email).first()
    if old and not old.is_del:
        return Fail(msg='该邮箱已被注册')
    if old:  # 已注销的同邮箱：恢复账号（换密码重激活）
        old.user_name = req.user_name.strip()
        old.password = get_password_hash(req.password)
        old.is_del = False
        await old.save()
        return Success(msg='账号已恢复，请登录')
    await User.create(
        user_name=req.user_name.strip(),
        email=req.email.strip(),
        password=get_password_hash(req.password),
        is_active=True,
    )
    return Success(msg='注册成功，请登录')


@router.post('/forgot_password', summary='忘记密码（发送重置链接到邮箱）')
async def forgot_password(req: VerifyCodeRequest):
    user = await User.filter(email=req.email, is_del=False).first()
    if not user:
        return Fail(msg='邮箱未注册')
    success, msg = await code_manager.generate_reset_token(req.email)
    return Fail(msg=msg) if not success else Success(msg=msg)


@router.post('/reset_password', summary='重置密码（令牌校验）')
async def reset_password(req: ResetPasswordRequest):
    success, msg = await code_manager.verify_reset_token(req.token)
    if not success:  # msg 此时是邮箱
        return Fail(msg=msg)
    user = await User.filter(email=msg, is_del=False).first()
    if not user:
        return Fail(msg='用户不存在')
    user.password = get_password_hash(req.new_password)
    await user.save()
    return Success(msg='密码重置成功')


# ============ 用户信息 / 改密 / 注销 ============


@router.get('/userinfo', summary='查看用户信息')
async def get_userinfo(user: User = DependAuth):
    data = await user.to_dict(exclude_fields=['password', 'id'])
    return Success(data=data)


@router.post('/update_password', summary='修改密码')
async def update_user_password(req: UpdatePassword, user: User = DependAuth):
    if not verify_password(req.old_password, user.password):
        return Fail(msg='旧密码验证错误！')
    user.password = get_password_hash(req.new_password)
    await user.save()
    return Success(msg='修改成功')


@router.post('/delete_account', summary='注销账号（软删除）')
async def delete_account(user: User = DependAuth):
    user.is_del = True
    await user.save(update_fields=['is_del'])
    return Success(msg='注销成功')
