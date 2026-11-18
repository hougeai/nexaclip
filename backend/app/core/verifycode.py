"""验证码 & 邮件基础设施。

- EmailManager：SMTP_SSL 连接，send_email 带连接保活 + 3 次重试
- CodeManager：邮箱验证码 / 密码重置令牌，改用**进程内 TTL 字典**（免费档不挂 Redis）
  ——验证码生命周期只有几分钟，容器重启丢了让用户重发即可；留好接口日后接 Redis 只换存储
- 不实现 SmsManager（手机短信服务，本项目不做手机登录）
"""

import secrets
import smtplib
import time
from email.mime.text import MIMEText
from typing import Dict, Tuple

from .config import settings
from .log import logger


class EmailManager:
    def __init__(self):
        self.email_host = settings.EMAIL_HOST
        self.email_port = settings.EMAIL_PORT
        self.email_user = settings.EMAIL_USER
        self.email_password = settings.EMAIL_PASSWORD
        self.sender = settings.EMAIL_SENDER
        self.smtp = None
        self._connect()

    def _connect(self):
        """建立 SMTP 连接"""
        try:
            self.smtp = smtplib.SMTP_SSL(self.email_host, self.email_port, timeout=10)
            self.smtp.login(self.email_user, self.email_password)
        except Exception as e:
            logger.error(f'SMTP 连接失败：{e}')
            self.smtp = None

    def _ensure_connection(self):
        """确保 SMTP 连接有效"""
        if self.smtp is None:
            self._connect()
        else:
            try:
                self.smtp.noop()
            except smtplib.SMTPServerDisconnected:
                logger.warning('SMTP 连接已断开，正在重新连接...')
                self._connect()

    def send_email(self, email: str, text: str, subject: str = '') -> Tuple[bool, str]:
        max_retries = 3
        for attempt in range(max_retries):
            self._ensure_connection()
            if self.smtp is None:
                return False, '无法建立 SMTP 连接'
            try:
                msg = MIMEText(text, 'plain', 'utf-8')
                msg['From'] = self.sender
                msg['To'] = email
                msg['Subject'] = subject
                self.smtp.sendmail(self.sender, email, msg.as_string())
                return True, '邮件已发送至您的邮箱'
            except Exception as e:
                try:
                    self.smtp.quit()
                except Exception:
                    pass
                self.smtp = None
                if attempt < max_retries - 1:
                    logger.warning(f'第{attempt + 1}次发送失败：{e}，准备重试')
                    continue
                logger.error(f'邮件发送失败（已重试{max_retries}次）：{e}')
                return False, '邮件发送失败，请重试'

    def __del__(self):
        if self.smtp:
            try:
                self.smtp.quit()
            except Exception as e:
                logger.error(f'关闭 SMTP 连接时出错：{e}')


class _TTLStore:
    """极简进程内 TTL 存储：{key: (value, expire_at)}。读写时惰性淘汰过期项。"""

    def __init__(self):
        self._data: Dict[str, Tuple[str, float]] = {}

    def set(self, key: str, value: str, ttl: int):
        self._data[key] = (value, time.time() + ttl)

    def get(self, key: str) -> str | None:
        item = self._data.get(key)
        if not item:
            return None
        value, expire_at = item
        if time.time() > expire_at:
            self._data.pop(key, None)
            return None
        return value

    def pop(self, key: str) -> str | None:
        value = self.get(key)
        self._data.pop(key, None)
        return value

    def incr(self, key: str, ttl: int) -> int:
        cur = int(self.get(key) or 0) + 1
        self.set(key, str(cur), ttl)
        return cur


class CodeManager:
    def __init__(self):
        self.email_manager = EmailManager()
        self.logo_name = settings.LOGO_NAME
        self._codes = _TTLStore()  # verify_code:{email} -> code
        self._attempts = _TTLStore()  # verify_attempt:{email} -> count
        self._reset = _TTLStore()  # reset_token:{token} -> email

    async def generate_code(self, email: str, expires_in: int = 10) -> Tuple[bool, str]:
        """为指定邮箱生成并发送验证码。expires_in 单位分钟。"""
        code = ''.join(secrets.choice('0123456789') for _ in range(6))
        text = f'您的验证码为：{code}，有效期为{expires_in}分钟。'
        subject = f'[no-reply] {self.logo_name} 验证码'
        success, msg = self.email_manager.send_email(email, text, subject)
        if success:
            self._codes.set(f'verify_code:{email}', code, expires_in * 60)
        return success, msg

    async def verify_code(self, email: str, code: str) -> Tuple[bool, str]:
        """验证邮箱验证码。连续错误 10 次锁定 60 秒。"""
        attempts = self._attempts.get(f'verify_attempt:{email}')
        if attempts and int(attempts) >= 10:
            return False, '验证码尝试次数过多，请稍后再试'

        stored_code = self._codes.get(f'verify_code:{email}')
        if not stored_code:
            return False, '请点击发送验证码'
        if stored_code != code:
            self._attempts.incr(f'verify_attempt:{email}', 60)
            return False, '验证码错误'

        self._codes.pop(f'verify_code:{email}')
        self._attempts.pop(f'verify_attempt:{email}')
        return True, '验证成功'

    async def generate_reset_token(self, email: str, expires_in: int = 60) -> Tuple[bool, str]:
        """为密码重置生成令牌并邮件下发。expires_in 单位分钟。"""
        reset_token = secrets.token_urlsafe(32)
        reset_link = f'{settings.USER_FE_URL}/reset-password?token={reset_token}'
        text = f"""您好，

您请求了密码重置。请点击以下链接重置您的密码：

{reset_link}

此链接将在 {expires_in} 分钟后失效。
如果您没有请求重置密码，请忽略此邮件。

祝好，
{self.logo_name} 团队
"""
        success, msg = self.email_manager.send_email(email, text, subject=f'{self.logo_name} 密码重置')
        if success:
            self._reset.set(f'reset_token:{reset_token}', email, expires_in * 60)
            return True, '重置链接已发送到您的邮箱'
        return False, msg

    async def verify_reset_token(self, token: str) -> Tuple[bool, str]:
        """验证重置令牌，成功返回 (True, email)。一次性使用，校验后即销毁。"""
        email = self._reset.pop(f'reset_token:{token}')
        if not email:
            return False, '重置链接无效或已过期，请回到登录页重试'
        return True, email
