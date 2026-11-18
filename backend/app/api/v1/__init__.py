from fastapi import APIRouter

from core.dependency import DependAuth

from .base import router as base_router
from .media import router as media_router
from .system import router as system_router

v1_router = APIRouter()
# base/system 含匿名接口（登录/注册/探活），不整体挂鉴权；media 全部需登录，路由级统一挂
v1_router.include_router(base_router, tags=['认证模块'], prefix='/base')
v1_router.include_router(system_router, tags=['系统模块'], prefix='/system')
# media 承载全流程：探测即自动下载、按 vid 轮询/历史/删除、成品下载、知识库检索问答（/media/kb/*）
v1_router.include_router(media_router, tags=['采集下载'], prefix='/media', dependencies=[DependAuth])
