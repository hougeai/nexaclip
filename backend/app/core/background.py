"""定时任务调度（APScheduler 单实例）。

系统任务：订阅检查（每个 UserSub.check_hour 一条 cron job，启动时扫描注册，运行期间动态增删）。
"""

import contextvars

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from tzlocal import get_localzone

from .log import logger
from .config import settings

# 请求级用户ID（API 层 set，controller 层 get）
CTX_USER_ID: contextvars.ContextVar[str] = contextvars.ContextVar('user_id', default='0')

# 调度器单例（lifespan 启动时创建，关闭时销毁）
_scheduler: AsyncIOScheduler | None = None


# ============ 调度器生命周期 ============
def setup_scheduler():
    """创建并启动调度器（lifespan 启动时调用）。"""
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)
    _scheduler.start()
    logger.info(f'定时任务调度器已启动，时区：{settings.TIMEZONE}')


def shutdown_scheduler():
    """关闭调度器（lifespan 关闭时调用）。"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info('定时任务调度器已关闭')


# ============ 订阅检查任务 ============
def register_sub_job(user_id: str, sid: str, check_hour: int) -> None:
    """为某个用户的某个订阅注册每日定点检查 cron job。"""
    if _scheduler is None:
        logger.warning('调度器未初始化，跳过注册订阅任务')
        return
    job_id = f'sub_check_{user_id}_{sid}'
    _scheduler.add_job(
        _run_sub_check,
        trigger=CronTrigger(hour=check_hour, minute=0, timezone=str(get_localzone())),
        id=job_id,
        name=f'订阅检查 {sid}（用户 {user_id}，{check_hour}:00）',
        replace_existing=True,
        args=[user_id, sid],
    )
    logger.info('注册订阅检查任务：sid=%s user_id=%s hour=%s', sid, user_id, check_hour)


def unregister_sub_job(user_id: str, sid: str) -> None:
    """移除某个用户的某个订阅的 cron job（check_hour 改为 null 或删除订阅时调用）。"""
    if _scheduler is None:
        return
    job_id = f'sub_check_{user_id}_{sid}'
    try:
        _scheduler.remove_job(job_id)
    except Exception:
        pass  # 任务不存在（未注册过）时忽略


async def _run_sub_check(user_id: str, sid: str):
    """cron job 回调：读该用户的 enum_limit 偏好后调 controller._sub_check。延迟 import 避免循环依赖。"""
    from controllers import video
    from models.video import UserSub

    try:
        us = await UserSub.filter(user_id=user_id, sid=sid).first()
        await video._sub_check(sid, us.enum_limit if us else None)
    except Exception:
        logger.exception('订阅检查任务执行异常：sid=%s', sid)


async def register_all_sub_jobs() -> None:
    """启动时扫描所有 UserSub，为 check_hour 非空的注册 cron job。需在 DB init 后调用。"""
    if _scheduler is None:
        logger.warning('调度器未初始化，跳过注册订阅任务')
        return
    from models.video import UserSub

    user_subs = await UserSub.filter(check_hour__isnull=False).values_list('user_id', 'sid', 'check_hour')
    for user_id, sid, check_hour in user_subs:
        register_sub_job(user_id, sid, check_hour)
    logger.info('启动时注册了 %d 个订阅检查任务', len(user_subs))
