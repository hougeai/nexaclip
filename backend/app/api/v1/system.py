from fastapi import APIRouter

from core.config import settings
from schemas.base import Success

router = APIRouter()


@router.api_route('/health', methods=['GET', 'HEAD'], summary='探活（兼冷启动预热）')
async def health():
    # HF Spaces 免费档 48h 无访问会休眠，前端启动即打这个接口唤醒
    return Success(data={'status': 'ok'})


@router.get('/config', summary='下发限制与配额')
async def config():
    return Success(
        data={
            'max_duration_seconds': settings.MAX_DURATION_SECONDS,
            'max_file_size': settings.MAX_FILE_SIZE,
            'allow_live': settings.ALLOW_LIVE,
            'download_concurrency': settings.DOWNLOAD_CONCURRENCY,
            'quota_concurrent_downloads': settings.QUOTA_CONCURRENT_DOWNLOADS,
        }
    )
