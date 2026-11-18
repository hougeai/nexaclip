from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.exceptions import (
    HTTPException,
    RequestValidationError,
    ResponseValidationError,
)
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware import Middleware
from tortoise import Tortoise
from tortoise.exceptions import DoesNotExist, IntegrityError

from api import api_router
from core import background, storage, vectorstore
from core.config import settings
from core.exceptions import (
    DoesNotExistHandle,
    HttpExcHandle,
    IntegrityHandle,
    RequestValidationHandle,
    ResponseValidationHandle,
)
from core.log import logger
from core.security import get_password_hash
from models.enums import AiStatus, DubStatus, EmbedStatus, HighlightStatus, VideoStatus
from models.user import User
from models.video import Video


def make_middlewares():
    """中间件必须在 app 构建期返回（Starlette 不允许启动后再 add_middleware）。

    跨域：全放开。token 走 Authorization/token 请求头（非 cookie），无需 allow_credentials，
    故可直接 allow_origins=['*']。成品下载走 /api/v1/media/{vid}/file 路由（FileResponse）。
    """
    return [
        Middleware(
            CORSMiddleware,
            allow_origins=['*'],
            allow_methods=['*'],
            allow_headers=['*'],
            max_age=3600,  # 缓存预检 1 小时，减少 OPTIONS 往返
        )
    ]


def register_exceptions(app: FastAPI):
    app.add_exception_handler(DoesNotExist, DoesNotExistHandle)
    app.add_exception_handler(HTTPException, HttpExcHandle)
    app.add_exception_handler(IntegrityError, IntegrityHandle)
    app.add_exception_handler(ResponseValidationError, ResponseValidationHandle)
    app.add_exception_handler(RequestValidationError, RequestValidationHandle)


def register_routers(app: FastAPI):
    # 最终路径统一为 /api/v1/...
    app.include_router(api_router, prefix='/api')


async def _recover_orphan_tasks():
    """重启后把非终态采集记录标记为失败：它们的后台协程已随进程消失，否则会永久卡在进行中。"""
    orphans = await Video.filter(
        status__in=[
            VideoStatus.QUEUED.value,
            VideoStatus.DOWNLOADING.value,
        ]
    ).update(
        status=VideoStatus.FAILED.value,
        error_msg='服务重启导致下载中断，请重新提交链接',
        finished_at=datetime.now(timezone.utc),
    )
    if orphans:
        logger.info('已中断 %d 个重启前未完成下载', orphans)
    # AI 报告协程同样随进程消失，running 的标 failed（转写结果保留，重新提交链接可续跑）
    ai_orphans = await Video.filter(ai_status=AiStatus.RUNNING.value).update(
        ai_status=AiStatus.FAILED.value,
        ai_error='服务重启导致 AI 分析中断，请重新提交链接',
    )
    if ai_orphans:
        logger.info('已中断 %d 个重启前未完成 AI 分析', ai_orphans)
    # 向量索引协程同样随进程消失，running 的标 failed（知识库页「补索引」一键可续跑）
    embed_orphans = await Video.filter(embed_status=EmbedStatus.RUNNING.value).update(
        embed_status=EmbedStatus.FAILED.value,
        embed_error='服务重启中断索引，请到知识库页补索引',
    )
    if embed_orphans:
        logger.info('已中断 %d 个重启前未完成向量索引', embed_orphans)
    # 高光标注协程同样随进程消失，running 的标 failed（详情页可重新点「标注高光」）
    hl_orphans = await Video.filter(highlight_status=HighlightStatus.RUNNING.value).update(
        highlight_status=HighlightStatus.FAILED.value,
        highlight_error='服务重启导致高光标注中断，请重新标注',
    )
    if hl_orphans:
        logger.info('已中断 %d 个重启前未完成高光标注', hl_orphans)
    # 配音合成协程同样随进程消失，running 的标 failed（详情页可重新发起）
    dub_orphans = await Video.filter(dub_status=DubStatus.RUNNING.value).update(
        dub_status=DubStatus.FAILED.value,
        dub_error='服务重启导致配音合成中断，请重新生成',
    )
    if dub_orphans:
        logger.info('已中断 %d 个重启前未完成配音合成', dub_orphans)


async def _init_superuser():
    if not settings.SUPER_ADMIN_EMAIL:
        return
    if await User.filter(email=settings.SUPER_ADMIN_EMAIL).exists():
        return
    # 固定 user_id='1'（对齐参考项目）：作为可预测的内置管理员标识，注册走 nanoid(8) 不会与之冲突
    await User.create(
        user_id='1',
        user_name=settings.SUPER_ADMIN_NAME,
        email=settings.SUPER_ADMIN_EMAIL,
        password=get_password_hash(settings.SUPER_ADMIN_PASSWORD),
        is_active=True,
    )
    logger.info('默认管理员已创建：%s (user_id=1)', settings.SUPER_ADMIN_EMAIL)


async def init_data(app: FastAPI):
    """lifespan 启动阶段：建数据目录、连库、建表、恢复孤儿任务、建默认管理员、起订阅调度。"""
    storage.ensure_dirs()
    await Tortoise.init(config=settings.TORTOISE_ORM)
    logger.info(f'已连接数据库：{settings.DB_TYPE}')
    await Tortoise.generate_schemas(safe=True)
    await vectorstore.setup()  # 挂 sqlite-vec 扩展 + 建 vec0/FTS5 虚表（幂等）
    await _recover_orphan_tasks()
    await _init_superuser()
    background.setup_scheduler()  # APScheduler 单实例（订阅检查 cron job 由此调度）
    await background.register_all_sub_jobs()  # 启动时扫描 UserSub，为 check_hour 非空的注册 cron job
