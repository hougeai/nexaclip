import logging

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from tortoise import Tortoise
from core import background
from core.init_app import init_data, make_middlewares, register_exceptions, register_routers
from core.config import settings
from core.log import logger


class InterceptHandler(logging.Handler):
    def emit(self, record):
        logger.log(record.levelno, record.getMessage(), exc_info=record.exc_info)


# 将uvicorn日志重定向到logger
for name in ('uvicorn', 'uvicorn.access'):
    log = logging.getLogger(name)
    log.handlers = [InterceptHandler()]
    log.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_data(app)
    yield
    background.shutdown_scheduler()  # 先停调度器，再关连接（避免退出竞态报连接错）
    await Tortoise.close_connections()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_TITLE,  # 设置 API 文档的标题
        description=settings.APP_DESCRIPTION,
        version=settings.VERSION,
        openapi_url='/openapi.json',  # OpenAPI (前身是 Swagger) 文档，包含了 API 的所有规范，FastAPI 自动生成的 API 文档的访问端点
        middleware=make_middlewares(),  # 调用这个函数获取中间件列表
        lifespan=lifespan,  # 传递函数本身作为引用
    )

    register_exceptions(app)  # 定义的异常都会以 JSONResponse 的形式返回错误响应
    register_routers(app)
    return app


app = create_app()

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=settings.BACK_END_PORT, reload=False, log_level='info', log_config=None)
