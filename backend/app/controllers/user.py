from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from core.security import get_password_hash, verify_password
from models.user import User
from schemas.login import CredentialsSchema, UserCreate, UserUpdate

from .crud import CRUDBase


# user controller：通过继承 CRUDBase 获得基础的 CRUD 操作
class UserController(CRUDBase[User, UserCreate, UserUpdate]):
    def __init__(self):
        super().__init__(model=User)

    async def get_by_user_id(self, user_id: str) -> Optional[User]:
        return await self.model.filter(user_id=user_id).first()

    async def get_by_username(self, user_name: str) -> Optional[User]:
        return await self.model.filter(user_name=user_name).first()

    async def get_by_email(self, email: str) -> Optional[User]:
        return await self.model.filter(email=email).first()

    async def create_user(self, obj_in: UserCreate) -> User:
        if obj_in.password:
            obj_in.password = get_password_hash(password=obj_in.password)
        obj = await self.create(obj_in)
        return obj

    async def authenticate(self, credentials: CredentialsSchema) -> User:
        user = await self.get_by_email(credentials.email)
        if not user or user.is_del:
            raise HTTPException(status_code=400, detail='邮箱未注册！')
        verified = verify_password(credentials.password, user.password)
        if not verified:
            raise HTTPException(status_code=400, detail='密码错误!')
        if not user.is_active:
            raise HTTPException(status_code=400, detail='用户已被禁用！')
        return user

    async def update_last_login(self, user_id: str) -> None:
        user = await self.get_by_user_id(user_id)
        if user:
            user.last_login = datetime.now(timezone.utc)
            await user.save(update_fields=['last_login'])


user_controller = UserController()
