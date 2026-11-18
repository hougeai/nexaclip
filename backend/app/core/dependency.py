import jwt
from typing import Optional

from fastapi import Depends, Header, HTTPException, Query

from models.user import User
from .background import CTX_USER_ID
from .config import settings


# 处理用户认证（header 或 ?token= 双入口，仅邮箱 JWT 认证）
class AuthControl:
    @staticmethod
    async def _resolve(token: str) -> User:
        try:
            decode_data = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=settings.JWT_ALGORITHM)
            user_id = decode_data.get('user_id')
            user = await User.filter(user_id=user_id).first()
            if not user:
                raise HTTPException(status_code=401, detail='Authentication failed')
            CTX_USER_ID.set(user.user_id)
            return user
        except jwt.DecodeError:
            raise HTTPException(status_code=401, detail='无效的Token')
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=419, detail='登录已过期')
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'{repr(e)}')

    @classmethod
    async def is_authed(
        cls,
        token: Optional[str] = Header(None, description='token验证（请求头）'),
        token_q: Optional[str] = Query(None, alias='token', description='token验证（查询参数，供浏览器 <a> 直接下载）'),
    ) -> User:
        """校验 token（请求头或 ?token= 任一），返回当前用户并写入 CTX_USER_ID。"""
        t = token or token_q
        if not t:
            raise HTTPException(status_code=401, detail='缺少 token')
        return await cls._resolve(t)


DependAuth = Depends(AuthControl.is_authed)
