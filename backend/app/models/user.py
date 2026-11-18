from tortoise import fields
from tortoise.exceptions import IntegrityError

from core.idgen import gen_id
from .base import BaseModel, TimestampMixin


class User(BaseModel, TimestampMixin):
    user_id = fields.CharField(
        max_length=12, null=True, unique=True, pk=False, description='用户唯一标识', index=True
    )  # 不是主键
    user_name = fields.CharField(max_length=20, description='用户名称', index=True)
    password = fields.CharField(max_length=128, null=True, description='密码')
    email = fields.CharField(max_length=255, null=True, unique=True, description='邮箱', index=True)
    is_active = fields.BooleanField(default=True, description='是否活跃', index=True)  # 冻结用户
    is_del = fields.BooleanField(default=False, description='是否注销（软删除）', index=True)
    last_login = fields.DatetimeField(null=True, description='最后登录时间', index=False)

    class Meta:
        table = 'user'

    async def save(self, *args, **kwargs):
        if not self.user_id:
            max_attempts = 5
            while max_attempts > 0:
                self.user_id = gen_id(8)
                try:
                    return await super().save(*args, **kwargs)
                except IntegrityError:
                    max_attempts -= 1
            raise ValueError('Failed to generate unique userid after attempts')
        return await super().save(*args, **kwargs)
