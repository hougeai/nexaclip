import asyncio
from typing import Literal

from fastapi import APIRouter, File, Query, Request, UploadFile

from controllers import video as video_controller
from core import rangeresp, storage, urlguard
from core.background import CTX_USER_ID
from core.log import logger
from core.ytdlp import ProbeError
from schemas.base import Fail, Success, SuccessExtra
from schemas.media import (
    HighlightClipReq,
    KbAskReq,
    KbSearchReq,
    ProbeReq,
    SubCreateReq,
    SubSettingsReq,
    TranslateReq,
    VideoMetaReq,
)

# 鉴权在 api/v1/__init__.py 路由级统一挂 DependAuth；此处需要 user_id 的地方取 CTX_USER_ID。
# 顺序敏感：/videos、/history、/stats、/kb/*、/subs 必须声明在 /{vid} 之前，否则被当成 vid 匹配。
router = APIRouter()


@router.post('/probe', summary='提交链接：探测元信息并自动下载（立即返回）')
async def probe(req: ProbeReq):
    """服务端 asyncio.gather 并发处理，逐条落库并起后台下载；单条失败不影响整批。"""
    try:
        urls = urlguard.validate_media_urls(req.urls)
    except urlguard.UrlError as e:
        return Fail(msg=str(e))

    user_id = CTX_USER_ID.get()

    async def one(url: str) -> dict:
        try:
            video = await video_controller.collect(user_id=user_id, url=url)
            return {'url': url, 'ok': True, **video_controller.to_out(video)}
        except (ProbeError, ValueError) as e:  # 配额/限制/探测失败
            logger.warning('采集失败 %s：%s', url, e)
            return {'url': url, 'ok': False, 'error': str(e)}
        except Exception as e:  # 兜底，不让单条炸整批
            logger.exception('采集异常 %s', url)
            return {'url': url, 'ok': False, 'error': f'处理失败：{e}'}

    results = await asyncio.gather(*(one(u) for u in urls))
    return Success(data=list(results))


@router.post('/upload', summary='本地上传视频（覆盖无法下载的视频；内容 hash 去重，直接进转写/报告/索引）')
async def upload(file: UploadFile = File(...)):
    """大小(≤500MB)/类型(video/*) 校验在前端拦；后端只兜底信任，落盘后按内容 hash 定 vid 去重。"""
    try:
        video, deduped = await video_controller.create_from_upload(CTX_USER_ID.get(), file)
    except ValueError as e:
        return Fail(msg=str(e))
    return Success(data={**video_controller.to_out(video), 'deduped': deduped})


@router.get('/videos', summary='批量轮询采集状态（ids=a,b,c）')
async def poll(ids: str = Query(..., description='逗号分隔的 vid')):
    vids = [i.strip() for i in ids.split(',') if i.strip()][:50]
    if not vids:
        return Success(data=[])
    items = await video_controller.list_by_ids(vids, CTX_USER_ID.get())
    return Success(data=[video_controller.to_out(v) for v in items])


@router.get('/history/groups', summary='平台→账号分组树（仅计数，供导航）')
async def history_groups():
    return Success(data=await video_controller.groups(CTX_USER_ID.get()))


@router.get('/history', summary='我的采集记录（分页可排序，可按平台/账号过滤）')
async def history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query(None),
    platform: str = Query(None, description='平台(extractor)；空串=未归类，缺省=不过滤'),
    uploader: str = Query(None, description='账号(uploader)；空串=未归类，缺省=不过滤'),
    sort: str = Query(
        'pub_desc',
        description='排序：pub_desc 最新发布在前 | pub_asc 最早发布在前 | coll_desc 最新采集在前 | coll_asc 最早采集在前',
    ),
):
    total, items = await video_controller.history(
        CTX_USER_ID.get(),
        page=page,
        page_size=page_size,
        status=status,
        platform=platform,
        uploader=uploader,
        sort=sort,
    )
    return SuccessExtra(
        data=[video_controller.to_out(v, lite=True) for v in items], total=total, page=page, page_size=page_size
    )


@router.get('/stats', summary='个人用量')
async def stats():
    return Success(data=await video_controller.stats(CTX_USER_ID.get()))


# 静态路径统一声明在 /{vid} 之前（路由顺序约定）；语种+音色表，前端选项单一真相源
@router.get('/translate/langs', summary='翻译支持的语种与候选音色')
async def translate_langs():
    return Success(data=video_controller.translate_langs())


# ============ 知识库（RAG）：全库检索 / 跨视频问答 / 回填索引 ============


@router.post('/kb/search', summary='知识库：全库检索片段（向量 + 关键词混合召回）')
async def kb_search(req: KbSearchReq):
    try:
        chunks = await video_controller.kb_search(
            CTX_USER_ID.get(), req.q, req.scope.model_dump() if req.scope else None, req.top_k
        )
    except (ProbeError, ValueError) as e:
        logger.warning('检索失败：%s', e)
        return Fail(msg=str(e))
    return Success(data=chunks)


@router.post('/kb/ask', summary='知识库：跨视频问答（答案带可点击时间戳引用）')
async def kb_ask(req: KbAskReq):
    try:
        data = await video_controller.kb_ask(
            CTX_USER_ID.get(),
            req.question,
            [t.model_dump() for t in req.history],
            req.scope.model_dump() if req.scope else None,
        )
    except (ProbeError, ValueError) as e:
        logger.warning('问答失败：%s', e)
        return Fail(msg=str(e))
    return Success(data=data)


@router.post('/kb/reindex', summary='知识库：一键回填索引（存量未向量化 / embedding 模型变更）')
async def kb_reindex():
    try:
        queued = await video_controller.kb_reindex(CTX_USER_ID.get())
    except ValueError as e:
        return Fail(msg=str(e))
    return Success(data={'queued': queued})


@router.get('/kb/status', summary='知识库：索引进度（供前端提示）')
async def kb_status():
    return Success(data=await video_controller.kb_status(CTX_USER_ID.get()))


# ============ 订阅：频道/作者主页 → 定时枚举 → 自动采集 ============


@router.post('/subs', summary='新增订阅（同步探测账号信息；失败不落库）')
async def sub_create(req: SubCreateReq):
    try:
        sub, us, info = await video_controller.create_subscription(CTX_USER_ID.get(), req.url)
    except (ProbeError, ValueError) as e:
        return Fail(msg=str(e))
    # probe：创建时探测到的账号信息（总数可能拿不到=null）；采集需用户手动点「立即采集」
    return Success(
        data={
            **video_controller.sub_out(sub, us),
            'probe': {
                k: info.get(k)
                for k in (
                    'total',
                    'signature',
                    'follower_count',
                    'total_favorited',
                    'unique_id',
                    'ip_location',
                    'city',
                    'country',
                    'room_id',
                    'mplatform_followers_count',
                )
            },
        }
    )


@router.get('/subs', summary='订阅列表（含全局头像映射 avatars，采集历史页用）')
async def sub_list():
    return Success(data=await video_controller.list_subscriptions(CTX_USER_ID.get()))


@router.post('/subs/{sid}/settings', summary='设置订阅偏好（每日定点检查小时 + 每轮枚举条数）')
async def sub_settings(sid: str, req: SubSettingsReq):
    try:
        sub = await video_controller.set_subscription_settings(CTX_USER_ID.get(), sid, req.hour, req.limit)
    except ValueError as e:
        return Fail(msg=str(e))
    return Success(data=sub)


@router.post('/subs/{sid}/check', summary='启动订阅检查（后台执行，新视频自动进采集队列）')
async def sub_check(sid: str):
    try:
        await video_controller.start_subscription_check(CTX_USER_ID.get(), sid)
    except ValueError as e:
        return Fail(msg=str(e))
    return Success(msg='检查已启动，完成后本页自动刷新')


@router.post('/subs/{sid}/reprobe', summary='重新探测账号信息（头像/简介/粉丝等，不采集视频）')
async def sub_reprobe(sid: str):
    try:
        body = await video_controller.reprobe_subscription(CTX_USER_ID.get(), sid)
    except ValueError as e:
        return Fail(msg=str(e))
    return Success(data=body)


@router.delete('/subs/{sid}', summary='删除订阅（不删已采视频）')
async def sub_remove(sid: str):
    if not await video_controller.remove_subscription(CTX_USER_ID.get(), sid):
        return Fail(code=404, msg='订阅不存在')
    return Success(msg='已删除')


@router.get('/{vid}', summary='单条详情（全量：含文案/章节/报告，供弹窗）')
async def detail(vid: str):
    video = await video_controller.get_owned(vid, CTX_USER_ID.get())
    if not video:
        return Fail(code=404, msg='记录不存在')
    return Success(data=video_controller.to_out(video))


@router.get('/{vid}/file', summary='成品/派生产物下载（206 Range；kind=video|sub|ass|dub）')
async def download_file(
    vid: str,
    request: Request,
    kind: Literal['video', 'sub', 'ass', 'dub'] = Query(
        'video', description='video 成品 | sub 双语srt | ass 烧录字幕 | dub 配音视频'
    ),
):
    video = await video_controller.get_owned(vid, CTX_USER_ID.get())
    if not video:
        return Fail(code=404, msg='记录不存在')
    if kind == 'video':
        path, mime = storage.local_path(vid), 'video/mp4'
    else:
        path = storage.derived_path(vid, kind)
        mime = {'sub': 'application/x-subrip', 'ass': 'text/x-ssa', 'dub': 'video/mp4'}[kind]
    if not path:
        return Fail(code=404, msg='文件不存在')
    return rangeresp.file_response(request, path, path.name, media_type=mime)


@router.delete('/{vid}', summary='删除采集记录与成品')
async def remove(vid: str):
    if not await video_controller.remove(vid, CTX_USER_ID.get()):
        return Fail(code=404, msg='记录不存在')
    return Success(msg='已删除')


@router.post('/{vid}/retranscribe', summary='重跑文案转写（转写失败后基于本地成品续跑）')
async def retranscribe(vid: str):
    try:
        await video_controller.retranscribe(vid, CTX_USER_ID.get())
    except ValueError as e:
        return Fail(msg=str(e))
    return Success(msg='已重新提交转写')


@router.post('/{vid}/reanalyze', summary='重跑 AI 报告（基于现有转写，不重复转写）')
async def reanalyze(vid: str):
    try:
        await video_controller.reanalyze(vid, CTX_USER_ID.get())
    except ValueError as e:
        return Fail(msg=str(e))
    return Success(msg='已重新提交 AI 分析')


@router.post('/{vid}/reembed', summary='重跑向量索引（索引失败/存量未索引的单视频续跑）')
async def reembed(vid: str):
    try:
        await video_controller.reembed(vid, CTX_USER_ID.get())
    except ValueError as e:
        return Fail(msg=str(e))
    return Success(msg='已重新提交索引')


@router.post('/{vid}/meta', summary='编辑视频标题 / 上传者（账号名）')
async def update_meta(vid: str, req: VideoMetaReq):
    try:
        video = await video_controller.update_video_meta(CTX_USER_ID.get(), vid, req.title, req.uploader)
    except ValueError as e:
        return Fail(msg=str(e))
    return Success(data=video_controller.to_out(video))


@router.post('/{vid}/highlights', summary='生成时间轴高光标注（手动触发，基于现有转写）')
async def gen_highlights(vid: str):
    try:
        await video_controller.gen_highlights(vid, CTX_USER_ID.get())
    except ValueError as e:
        return Fail(msg=str(e))
    return Success(msg='已提交高光标注')


@router.post('/{vid}/translate', summary='生成双语字幕（同步：译文 srt + 烧录用 ass；源语言自动检测）')
async def translate(vid: str, req: TranslateReq):
    try:
        data = await video_controller.translate_srt(vid, CTX_USER_ID.get(), req.lang_target)
    except (ProbeError, ValueError) as e:
        return Fail(msg=str(e))
    return Success(data=data)


@router.post('/{vid}/dub', summary='生成双语配音视频（异步：TTS 自然语速译音替换原音 + 烧译文字幕）')
async def dub(vid: str, req: TranslateReq):
    try:
        await video_controller.start_dub(vid, CTX_USER_ID.get(), req.lang_target, req.voice)
    except (ProbeError, ValueError) as e:
        return Fail(msg=str(e))
    return Success(msg='配音生成已启动，完成后本页自动刷新')


@router.post('/{vid}/highlights/clip', summary='导出选中高光片段（merge=拼成一个 mp4，否则分条打包 zip）')
async def export_clip(vid: str, req: HighlightClipReq):
    try:
        name = await video_controller.export_highlight_clips(
            vid, CTX_USER_ID.get(), [p.model_dump() for p in req.points], req.merge
        )
    except ValueError as e:
        return Fail(msg=str(e))
    return Success(data={'file': name})


@router.get('/{vid}/clip/{name}', summary='下载高光切片产物（mp4 / zip）')
async def download_clip(vid: str, name: str, request: Request):
    if not await video_controller.get_owned(vid, CTX_USER_ID.get()):
        return Fail(code=404, msg='记录不存在')
    path = storage.clip_path(vid, name)
    if not path:
        return Fail(code=404, msg='切片不存在')
    media_type = 'application/zip' if name.endswith('.zip') else 'video/mp4'
    return rangeresp.file_response(request, path, name, media_type=media_type)
