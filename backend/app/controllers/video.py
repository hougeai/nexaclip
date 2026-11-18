"""采集编排：全局媒体库 + 用户归属（共享去重的核心设计）。

设计要点：
- Video 全局一行/平台视频：一份文件、一份转写、一份报告、一份向量；用户归属走 UserVid 关联表
- 同视频多用户只下载一次（命中即挂接）；删除按引用计数，最后一个 UserVid 消失才清库
- 部署固定 --workers 1，进程内 dict 存进度/任务句柄安全（key=vid，全局唯一）
- 用户只提交链接：探测到元信息即入库并起后台下载；订阅轮询复用同一 collect 链
- 成品本地永久留存，download_url 恒为 /api/v1/media/{vid}/file 路由（不落库，读取时拼）
"""

import asyncio
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from tortoise.exceptions import IntegrityError
from tortoise.expressions import Case, Q, When
from tortoise.functions import Count

from core import (
    ai_report,
    background,
    bilibili,
    douyin,
    highlight,
    langdetect,
    llm,
    rag,
    storage,
    translate as translate_mod,
    transcript,
    urlguard,
    vectorstore,
    ytdlp,
)
from core.idgen import gen_id
from core.config import settings
from core.log import logger
from models.enums import AiStatus, Channel, DubStatus, EmbedStatus, HighlightStatus, VideoStatus
from models.video import Subscription, UserSub, UserVid, Video, VideoChunk

_TZ = ZoneInfo(settings.TIMEZONE)

# 内存进度缓存/任务句柄：key=vid（平台视频 ID，全局唯一）
_progress: dict[str, dict] = {}
_running: dict[str, asyncio.Task] = {}
# 全局并发下载闸（免费档 2 vCPU，默认 2）
_semaphore = asyncio.Semaphore(settings.DOWNLOAD_CONCURRENCY)


# ============ 限制与配额 ============


async def _user_vids(user_id: str) -> list[str]:
    """用户库 vid 集合（一切按用户过滤的查询入口）。"""
    return list(await UserVid.filter(user_id=user_id).values_list('vid', flat=True))


async def _check_quota(user_id: str) -> None:
    """用户维度配额：自己库中进行中（queued/downloading）的视频数。超限抛 ValueError（api 层转 Fail）。"""
    vids = await _user_vids(user_id)
    active = await Video.filter(
        vid__in=vids,
        status__in=[VideoStatus.QUEUED.value, VideoStatus.DOWNLOADING.value],
    ).count()
    if active >= settings.QUOTA_CONCURRENT_DOWNLOADS:
        raise ValueError(f'同时进行中的下载已达上限（{settings.QUOTA_CONCURRENT_DOWNLOADS}）')


def _check_limits(info: dict) -> None:
    """探测阶段体量限制：时长、预估体积、直播。统一按最佳画质估体积。"""
    if info.get('is_live') and not settings.ALLOW_LIVE:
        raise ValueError('不支持下载直播流')
    duration = info.get('duration')
    if duration and duration > settings.MAX_DURATION_SECONDS:
        raise ValueError(f'视频过长（{int(duration)}s，上限 {settings.MAX_DURATION_SECONDS}s）')
    sizes = [q['filesize'] for q in (info.get('qualities') or []) if q.get('filesize')]
    est = max(sizes) if sizes else None
    if est and est > settings.MAX_FILE_SIZE:
        raise ValueError(f'预估体积过大（约 {est // 1024 // 1024}MB，上限 {settings.MAX_FILE_SIZE // 1024 // 1024}MB）')


# ============ 采集入口（探测 + 自动下载 + 归属挂接） ============


def _chan_for(url: str) -> tuple[str, object]:
    """按域名分流渠道：返回 (Channel 值, 渠道模块)。"""
    if douyin.is_douyin_url(url):
        return Channel.DOUYIN.value, douyin
    if bilibili.is_bilibili_url(url):
        return Channel.BILIBILI.value, bilibili
    return Channel.YTDLP.value, ytdlp


# 瞬态错误特征：风控/频率限制类失败，间隔重试通常可恢复（文案由渠道层归一，按特征匹配）
_TRANSIENT_PATTERNS = ('风控', '频繁', '超时', '稍后重试')


def _is_transient(e: Exception) -> bool:
    """判断探测错误是否瞬态。链接本身有问题（不支持/404/私密/年龄限制）不算，不重试。"""
    msg = str(e)
    return any(p in msg for p in _TRANSIENT_PATTERNS)


async def _probe_retry(chan, url: str) -> dict:
    """探测带重试：风控/频率限制是瞬态的，间隔重试通常可恢复。

    最多尝试 settings.PROBE_ATTEMPTS 次，间隔按次数线性递增（2s/4s/6s/8s）；
    非瞬态错误或已到最后一次，立即抛出，不做无谓等待。
    """
    for attempt in range(1, settings.PROBE_ATTEMPTS + 1):
        try:
            return await chan.probe(url)
        except ytdlp.ProbeError as e:
            if not _is_transient(e) or attempt == settings.PROBE_ATTEMPTS:
                raise
            logger.warning('探测瞬态失败（%d/%d）%s：%s', attempt, settings.PROBE_ATTEMPTS, url, e)
            await asyncio.sleep(settings.PROBE_RETRY_INTERVAL * attempt)


async def collect(
    user_id: str, url: str, check_quota: bool = True, download_url: str | None = None, info: dict | None = None
) -> Video:
    """校验 → 命中复用/挂接 → 否则探测入库 → 起后台下载 → 挂 UserVid。返回库行。

    命中规则：source_url 快路径（免探测）→ 平台 ID 兜底（短链/全链归一化去重）；
    库里已有则只挂接不下载（共享去重的核心收益）；失败行重下=全局修复，惠及所有引用者。
    check_quota=False：订阅后台采集用（系统任务，全局 Semaphore 兜并发，不占个人配额）。
    download_url：真实 CDN 下载地址（订阅采集时从 list_user_latest 拿到）。
    info：订阅采集时从 list_user_latest 拿到的完整探测快照，传入则跳过重复 probe。
    """
    url = urlguard.validate_media_url(url)
    channel, chan = _chan_for(url)
    lib = await Video.filter(source_url=url).first()
    if lib:
        await UserVid.get_or_create(user_id=user_id, vid=lib.vid)
        return await _reuse_existing(lib, url, user_id, channel, check_quota, download_url)

    if check_quota:
        await _check_quota(user_id)
    if info is None:
        info = await _probe_retry(chan, url)  # 瞬态风控自动重试，全部失败才抛 ProbeError
    _check_limits(info)
    vid = info.get('id')
    if not vid:
        raise ytdlp.ProbeError('无法识别平台视频 ID')

    lib = await Video.filter(vid=vid).first()
    if lib:  # 别人/自己经别的链接采过 → 挂接即完成，零下载
        await UserVid.get_or_create(user_id=user_id, vid=vid)
        return await _reuse_existing(lib, url, user_id, channel, check_quota, download_url)

    try:
        video = await Video.create_from_info(url, info, channel, vid=vid)
    except IntegrityError:  # 并发竞态：他人刚建同 vid → 挂接复用
        video = await Video.filter(vid=vid).first()
        if not video:
            raise ytdlp.ProbeError('采集冲突，请重试')
        await UserVid.get_or_create(user_id=user_id, vid=vid)
        return await _reuse_existing(video, url, user_id, channel, check_quota, download_url)
    await UserVid.get_or_create(user_id=user_id, vid=vid)
    _running[vid] = asyncio.create_task(_run(vid, url, channel, download_url))
    return video


async def _reuse_existing(
    video: Video, url: str, user_id: str, channel: str, check_quota: bool = True, download_url: str | None = None
) -> Video:
    """命中库内已有的统一出口：进行中/已完成原样返回；失败复用快照重下（全局修复）。"""
    if video.status in (VideoStatus.QUEUED.value, VideoStatus.DOWNLOADING.value, VideoStatus.READY.value):
        return video
    if check_quota:
        await _check_quota(user_id)
    video.status = VideoStatus.QUEUED.value
    video.percent = None
    video.error_msg = None
    await video.save(update_fields=['status', 'percent', 'error_msg'])
    _running[video.vid] = asyncio.create_task(_run(video.vid, url, channel, download_url))
    return video


# ============ 本地上传（channel=upload：无取流，文件直落 store 走下游管线）============
async def create_from_upload(user_id: str, upload) -> tuple[Video, bool]:
    """接收上传文件 → 流式落工作区同时算 sha256 → vid=hash[:20]（内容去重）。

    返回 (Video, deduped)。命中已有 vid：只挂 UserVid、删临时文件、返回已有记录 + deduped=True。
    新文件：移入 store/ → 建 Video(status=ready)+UserVid → 起 _transcribe（后续报告/索引自动跟进）+ deduped=False。
    大小/类型校验在前端拦，后端只兜底信任。upload 为 fastapi.UploadFile（鸭子类型：.filename / .read / .close）。
    """
    ext = (Path(upload.filename or '').suffix or '.mp4').lower()
    tmp = storage.WORK_DIR / f'up_{gen_id(10)}{ext}'
    storage.WORK_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    try:
        with tmp.open('wb') as f:
            while chunk := await upload.read(1024 * 1024):
                h.update(chunk)
                f.write(chunk)
    finally:
        await upload.close()

    vid = h.hexdigest()[:20]
    existing = await Video.filter(vid=vid).first()
    if existing:  # 内容命中：复用，不重复存文件/转写
        tmp.unlink(missing_ok=True)
        await UserVid.get_or_create(user_id=user_id, vid=vid)
        logger.info('上传命中已有 %s（仅挂接）', vid)
        return existing, True

    file_size = tmp.stat().st_size  # save 前取（save 会把临时文件移走）
    await storage.save(str(tmp), vid=vid)  # 移入 store/{vid}{ext}
    duration = await _ffprobe_duration(vid)
    video = await Video.create(
        vid=vid,
        source_url=f'upload://{vid}',
        title=Path(upload.filename or vid).stem,
        channel=Channel.UPLOAD.value,
        extractor='Upload',
        duration=duration,
        file_name=f'{vid}{ext}',
        file_size=file_size,
        status=VideoStatus.READY.value,
        percent=100,
        finished_at=datetime.now(timezone.utc),
    )
    await UserVid.create(user_id=user_id, vid=vid)
    asyncio.create_task(_transcribe(vid))  # 直接进转写 → 报告 → 索引
    logger.info('上传入库完成 %s -> %s', vid, video.file_name)
    return video, False


async def _ffprobe_duration(vid: str) -> Optional[int]:
    """ffprobe 探成品时长（秒，向下取整）；失败返回 None（不阻断入库）。"""
    path = storage.local_path(vid)
    if not path:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            'ffprobe',
            '-v',
            'error',
            '-show_entries',
            'format=duration',
            '-of',
            'default=noprint_wrappers=1:nokey=1',
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return int(float(out.decode().strip())) if proc.returncode == 0 and out.strip() else None
    except Exception:
        logger.warning('ffprobe 探时长失败 %s', vid)
        return None


async def update_video_meta(user_id: str, vid: str, title: Optional[str], uploader: Optional[str]) -> Video:
    """编辑视频标题 / 上传者（账号名）。仅改已有字段，None 表示不动。

    uploader 影响采集记录页分组（按账号归集）；上传件默认无 uploader，用户可补。
    """
    video = await get_owned(vid, user_id)
    if not video:
        raise ValueError('记录不存在')
    fields = []
    if title is not None and (title := title.strip()):
        video.title = title[:500]
        fields.append('title')
    if uploader is not None and (uploader := uploader.strip()):
        video.uploader = uploader[:200]
        fields.append('uploader')
    if fields:
        await video.save(update_fields=fields)
    return video


async def _run(vid: str, url: str, channel: str, download_url: str | None = None):
    """后台协程：下载（最佳画质）→ 移入 store/ → 置 ready。全程异常吞掉并落 failed，不冒泡。"""
    video = await Video.filter(vid=vid).first()
    if not video:
        return
    try:
        async with _semaphore:
            video.status = VideoStatus.DOWNLOADING.value
            video.started_at = datetime.now(timezone.utc)
            await video.save(update_fields=['status', 'started_at'])
            _progress[vid] = {'percent': 0}

            def on_progress(p: dict):
                cache = _progress.setdefault(vid, {})
                cache.update({k: v for k, v in p.items() if k in ('percent', 'speed', 'eta')})

            chan = (
                douyin
                if channel == Channel.DOUYIN.value
                else (bilibili if channel == Channel.BILIBILI.value else ytdlp)
            )
            # download_url 优先（订阅采集时 list_user_latest 已拿到真实 CDN 地址，免二次探测）
            result = await chan.download(download_url or url, vid=vid, on_progress=on_progress)

            await storage.save(result['path'], vid=vid)
            video.file_name = result['filename']
            video.file_size = result['size']

        video.status = VideoStatus.READY.value
        video.percent = 100
        video.finished_at = datetime.now(timezone.utc)
        await video.save()
        logger.info('采集完成 %s -> %s', vid, video.file_name)
        asyncio.create_task(_transcribe(vid))  # 下载完成即异步转写，不阻塞 ready 状态
    except asyncio.CancelledError:
        await _mark_failed(vid, '已取消')
        logger.info('下载已取消 %s', vid)
    except ytdlp.ProbeError as e:
        logger.warning('下载失败 %s：%s', vid, e)
        await _mark_failed(vid, str(e))
    except Exception as e:
        logger.exception('下载异常 %s', vid)
        await _mark_failed(vid, f'内部错误：{e}')
    finally:
        _progress.pop(vid, None)
        _running.pop(vid, None)
        # 清工作区残留（失败/取消时 kill 进程会留下 .part/.fNNN 中间件；成功时成品已被 storage.save 移走）
        for p in storage.WORK_DIR.glob(f'{vid}.*'):
            p.unlink(missing_ok=True)


async def _mark_failed(vid: str, msg: str):
    video = await Video.filter(vid=vid).first()
    if video:
        video.status = VideoStatus.FAILED.value
        video.error_msg = msg[:500]
        video.finished_at = datetime.now(timezone.utc)
        await video.save(update_fields=['status', 'error_msg', 'finished_at'])


async def _transcribe(vid: str):
    """下载成品 → 提取音频 → ASR 转写，结果落 transcript / transcript_segments / transcript_error。全程不冒泡。"""
    video = await Video.filter(vid=vid).first()
    if not video:
        return
    try:
        result = await transcript.extract_from_store(vid)
        video.transcript = result['text']
        video.transcript_segments = result['segments'] or None
        video.transcript_error = None
        video.lang_source = langdetect.detect_lang(result['text'])  # 源语言是转写元数据，自动检测
        # 转写成功即置 AI/索引 running（同一次 save，避免轮询看到「转写完成但下游未开始」的窗口）
        video.ai_status = AiStatus.RUNNING.value
        video.ai_error = None
        video.embed_status = EmbedStatus.RUNNING.value
        video.embed_error = None
        await video.save(
            update_fields=[
                'transcript',
                'transcript_segments',
                'transcript_error',
                'lang_source',
                'ai_status',
                'ai_error',
                'embed_status',
                'embed_error',
            ]
        )
        logger.info('文案转写完成 %s（%d 字 / %d 段）', vid, len(video.transcript), len(result['segments']))
    except transcript.ProbeError as e:
        video.transcript_error = str(e)[:500]
        await video.save(update_fields=['transcript_error'])
        logger.warning('文案转写失败 %s：%s', vid, e)
        return
    except Exception:
        logger.exception('文案转写异常 %s', vid)
        video.transcript_error = '文案提取失败，请稍后重试'
        await video.save(update_fields=['transcript_error'])
        return

    asyncio.create_task(_ai_analyze(vid))
    asyncio.create_task(_embed(vid))  # 向量索引与报告并行，两者只依赖 segments 互不依赖


async def _embed(vid: str):
    """转写分块 → embedding → 三处写入（video_chunk 主表 / FTS / vec0）。全程不冒泡。"""
    video = await Video.filter(vid=vid).first()
    if not video or not video.transcript_segments:
        return
    try:
        segments = video.transcript_segments
        chunks = transcript.chunk_segments(segments)
        if not chunks:
            raise ytdlp.ProbeError('无转写内容可索引')
        vectors = await llm.embed_texts([c['text'] for c in chunks])
        # 幂等重建：先清旧索引行（delete_vid 依赖主表查 id，必须在删主表之前）
        await vectorstore.delete_vid(vid)
        await VideoChunk.filter(vid=vid).delete()
        rows = []
        for seq, (c, vec) in enumerate(zip(chunks, vectors)):
            rec = await VideoChunk.create(
                vid=vid,
                seq=seq,
                text=c['text'],
                start=c['start'],
                end=c['end'],
                model=settings.EMBED_MODEL,
            )
            rows.append({'id': rec.id, 'vid': vid, 'text': c['text'], 'embedding': llm.pack(vec)})
        await vectorstore.insert_chunks(rows)
        video.embed_status = EmbedStatus.DONE.value
        video.embed_error = None
        await video.save(update_fields=['embed_status', 'embed_error'])
        logger.info('向量索引完成 %s（%d 块）', vid, len(rows))
    except ytdlp.ProbeError as e:
        video.embed_status = EmbedStatus.FAILED.value
        video.embed_error = str(e)[:500]
        await video.save(update_fields=['embed_status', 'embed_error'])
        logger.warning('向量索引失败 %s：%s', vid, e)
    except Exception:
        logger.exception('向量索引异常 %s', vid)
        video.embed_status = EmbedStatus.FAILED.value
        video.embed_error = '索引失败，请稍后重试'
        await video.save(update_fields=['embed_status', 'embed_error'])


async def _ai_analyze(vid: str):
    """转写分句 → LLM 大纲+章节 → 图文报告，落 ai_chapters / ai_report / ai_status。全程不冒泡。"""
    video = await Video.filter(vid=vid).first()
    if not video or not video.transcript_segments:
        return
    try:
        segments = video.transcript_segments
        result = await ai_report.analyze(segments)
        video.ai_chapters = result['chapters']
        video.ai_report = result['report']
        video.ai_status = AiStatus.DONE.value
        video.ai_error = None
        await video.save(update_fields=['ai_chapters', 'ai_report', 'ai_status', 'ai_error'])
        logger.info('AI 报告完成 %s（%d 章节 / %d 字）', vid, len(result['chapters']), len(result['report']))
    except ai_report.ProbeError as e:
        video.ai_status = AiStatus.FAILED.value
        video.ai_error = str(e)[:500]
        await video.save(update_fields=['ai_status', 'ai_error'])
        logger.warning('AI 报告失败 %s：%s', vid, e)
    except Exception:
        logger.exception('AI 报告异常 %s', vid)
        video.ai_status = AiStatus.FAILED.value
        video.ai_error = 'AI 分析失败，请稍后重试'
        await video.save(update_fields=['ai_status', 'ai_error'])


# ============ 查询 / 删除（用户视角都经 UserVid 归属过滤） ============


def to_out(video: Video, lite: bool = False) -> dict:
    """库行 → 前端返回体。合并内存进度；ready 给本地下载路由。

    lite=True 省略文案/报告大字段（列表卡片用），点开详情另走单条接口拉全量。
    """
    cache = _progress.get(video.vid, {})
    download_url = f'/api/v1/media/{video.vid}/file' if video.status == VideoStatus.READY.value else None
    out = {
        'vid': video.vid,
        'status': video.status,
        'percent': cache.get('percent', video.percent),
        'speed': cache.get('speed'),
        'eta': cache.get('eta'),
        'url': video.source_url,
        'title': video.title,
        'uploader': video.uploader,
        'duration': video.duration,
        'thumbnail': video.thumbnail,
        'extractor': video.extractor,
        'channel': video.channel,
        'publish_at': video.publish_at,
        'filename': video.file_name,
        'size': video.file_size,
        'download_url': download_url,
        'error': video.error_msg,
        # 轻量标量：列表卡片渲染 stepper / 文案·章节角标用，不含正文
        'ai_status': video.ai_status,
        'embed_status': video.embed_status,
        'highlight_status': video.highlight_status,
        'dub_status': video.dub_status,
        'lang_source': video.lang_source,
        'transcript_error': video.transcript_error,
        'transcript_len': len(video.transcript) if video.transcript else None,
        'ai_chapters_count': len(video.ai_chapters or []),
        'created_at': video.create_at.strftime(settings.DATETIME_FORMAT) if video.create_at else None,
    }
    if not lite:
        out.update(
            {
                # 转写正文 + 时间轴、AI 章节明细与报告全文（仅详情接口返回）
                'transcript': video.transcript,
                'transcript_segments': video.transcript_segments or [],
                'ai_chapters': video.ai_chapters or [],
                'ai_report': video.ai_report,
                'ai_error': video.ai_error,
                'highlights': video.highlights or [],
                'highlight_error': video.highlight_error,
                'dub_error': video.dub_error,
                'dub_lang': video.dub_lang,
                'dub_voice': video.dub_voice,
                # 双语字幕是否已生成（配音的前置确认步骤）
                'sub_exists': bool(storage.derived_path(video.vid, 'sub')),
            }
        )
    return out


async def list_by_ids(vids: list[str], user_id: str) -> list[Video]:
    owned = await UserVid.filter(user_id=user_id, vid__in=vids).values_list('vid', flat=True)
    return await Video.filter(vid__in=list(owned)).all()


async def get_owned(vid: str, user_id: str) -> Optional[Video]:
    if not await UserVid.filter(user_id=user_id, vid=vid).exists():
        return None
    return await Video.filter(vid=vid).first()


async def retranscribe(vid: str, user_id: str) -> None:
    """转写失败后重跑（成品仍在，只重起 _transcribe 链，成功后 AI 报告/索引自动跟进）。

    修复是全局的（惠及所有引用者），但入口要求调用方拥有该视频。
    """
    video = await get_owned(vid, user_id)
    if not video:
        raise ValueError('记录不存在')
    if not storage.local_path(vid):
        raise ValueError('成品文件不存在，请重新采集')
    if video.transcript and not video.transcript_error:
        raise ValueError('文案已转写完成')
    if not video.transcript_error:  # 转写中（error/transcript 双空）也拦，防重复起协程
        raise ValueError('文案转写进行中，请等待完成')
    video.transcript_error = None
    await video.save(update_fields=['transcript_error'])
    asyncio.create_task(_transcribe(vid))


async def reanalyze(vid: str, user_id: str) -> None:
    """AI 报告失败后重跑（基于现有转写续跑，不重复转写）。

    修复是全局的（惠及所有引用者），但入口要求调用方拥有该视频。
    """
    video = await get_owned(vid, user_id)
    if not video:
        raise ValueError('记录不存在')
    if not video.transcript_segments:
        raise ValueError('转写尚未完成，请先重跑转写')
    if video.ai_status == AiStatus.RUNNING.value:
        raise ValueError('AI 分析进行中，请等待完成')
    video.ai_status = AiStatus.RUNNING.value
    video.ai_error = None
    await video.save(update_fields=['ai_status', 'ai_error'])
    asyncio.create_task(_ai_analyze(vid))


async def reembed(vid: str, user_id: str) -> None:
    """重跑向量索引（索引失败/存量未索引时单视频续跑，基于现有转写）。

    正常链路转写完自动起 _embed，本接口只兜底失败与历史未索引的视频。
    """
    video = await get_owned(vid, user_id)
    if not video:
        raise ValueError('记录不存在')
    if not video.transcript_segments:
        raise ValueError('转写尚未完成，无法索引')
    if video.embed_status == EmbedStatus.RUNNING.value:
        raise ValueError('索引进行中，请等待完成')
    video.embed_status = EmbedStatus.RUNNING.value
    video.embed_error = None
    await video.save(update_fields=['embed_status', 'embed_error'])
    asyncio.create_task(_embed(vid))


# ============ 时间轴高光标注（用户手动触发，报告完成后才值得标） ============


async def gen_highlights(vid: str, user_id: str) -> None:
    """起后台高光标注（基于现有转写，与报告互不依赖，可重复跑覆盖旧结果）。"""
    video = await get_owned(vid, user_id)
    if not video:
        raise ValueError('记录不存在')
    if not video.transcript_segments:
        raise ValueError('转写尚未完成，无法标注高光')
    if video.highlight_status == HighlightStatus.RUNNING.value:
        raise ValueError('高光标注进行中，请等待完成')
    video.highlight_status = HighlightStatus.RUNNING.value
    video.highlight_error = None
    await video.save(update_fields=['highlight_status', 'highlight_error'])
    asyncio.create_task(_highlight(vid))


async def _highlight(vid: str):
    """转写分块 → LLM 挑高亮点，落 highlights / highlight_status。全程不冒泡。"""
    video = await Video.filter(vid=vid).first()
    if not video or not video.transcript_segments:
        return
    try:
        segments = video.transcript_segments
        points = await highlight.score(segments)
        video.highlights = points
        video.highlight_status = HighlightStatus.DONE.value
        video.highlight_error = None
        await video.save(update_fields=['highlights', 'highlight_status', 'highlight_error'])
        logger.info('高光标注完成 %s（%d 个点）', vid, len(points))
    except highlight.ProbeError as e:
        video.highlight_status = HighlightStatus.FAILED.value
        video.highlight_error = str(e)[:500]
        await video.save(update_fields=['highlight_status', 'highlight_error'])
        logger.warning('高光标注失败 %s：%s', vid, e)
    except Exception:
        logger.exception('高光标注异常 %s', vid)
        video.highlight_status = HighlightStatus.FAILED.value
        video.highlight_error = '高光标注失败，请稍后重试'
        await video.save(update_fields=['highlight_status', 'highlight_error'])


async def export_highlight_clips(vid: str, user_id: str, points: list[dict], merge: bool) -> str:
    """把选中的高亮点切成 mp4：merge=True 拼成一个高光集，False 分条打包 zip。

    返回产物文件名（前端经 /media/{vid}/clip/{name} 下载）。
    同步执行（-c copy 零转码，秒级），在请求内完成，不走后台协程。
    """
    video = await get_owned(vid, user_id)
    if not video:
        raise ValueError('记录不存在')
    src = storage.local_path(vid)
    if not src:
        raise ValueError('成品文件不存在，请重新采集')
    spans = []
    for p in points:
        try:
            start, end = float(p['start']), float(p['end'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('高亮点时间戳非法')
        if end <= start:
            raise ValueError('高亮点时间戳非法')
        spans.append((start, end))
    if not spans:
        raise ValueError('请先选择要导出的高光片段')
    if len(spans) > 20:
        raise ValueError('单次最多导出 20 个片段')
    spans.sort()
    return await storage.cut_clips(src, vid, spans, merge=merge)


# ============ 视频翻译 / 双语字幕 / 配音（用户手动触发，zh/en/ja/ko 四语种） ============


def translate_langs() -> dict:
    """语种 + 候选音色表（前端选项单一真相源，经 GET /media/translate/langs 下发）。"""
    return {code: {'name': v['name'], 'voices': v['voices']} for code, v in translate_mod.LANGS.items()}


async def translate_srt(vid: str, user_id: str, lang_target: str) -> dict:
    """生成双语字幕（同步，LLM 秒级~分钟级）：srt 供下载对照，ass 供烧录（只含译文）。

    源语言用转写时自动检测的 lang_source，用户只选目标语言。
    产物 store/{vid}_sub.srt / {vid}_ass.ass，经 /media/{vid}/file?kind=sub|ass 取。
    """
    video = await get_owned(vid, user_id)
    if not video:
        raise ValueError('记录不存在')
    if not video.transcript_segments:
        raise ValueError('转写尚未完成，无法翻译')
    pairs = await translate_mod.translate_segments(video.transcript_segments, video.lang_source, lang_target)
    (storage.STORE_DIR / f'{vid}_sub.srt').write_text(translate_mod.build_srt(pairs), encoding='utf-8')
    (storage.STORE_DIR / f'{vid}_ass.ass').write_text(translate_mod.build_ass(pairs), encoding='utf-8')
    return {
        'srt_url': f'/api/v1/media/{vid}/file?kind=sub',
        'translated': sum(1 for p in pairs if p['target']),
        'total': len(pairs),
    }


async def start_dub(vid: str, user_id: str, lang_target: str, voice: Optional[str] = None) -> None:
    """起后台配音管线（翻译→逐句 TTS→绝对时点叠轨→替换原音+烧字幕 → store/{vid}_dub.mp4）。"""
    video = await get_owned(vid, user_id)
    if not video:
        raise ValueError('记录不存在')
    if not video.transcript_segments:
        raise ValueError('转写尚未完成，无法配音')
    if not storage.local_path(vid):
        raise ValueError('成品文件不存在，请重新采集')
    if video.dub_status == DubStatus.RUNNING.value:
        raise ValueError('配音生成中，请等待完成')
    translate_mod.check_pair(video.lang_source, lang_target)
    resolved = translate_mod.resolve_voice(lang_target, voice)
    video.dub_status = DubStatus.RUNNING.value
    video.dub_error = None
    await video.save(update_fields=['dub_status', 'dub_error'])
    asyncio.create_task(_run_dub(vid, lang_target, resolved))


async def _run_dub(vid: str, lang_target: str, voice: str):
    """配音后台管线：全程不冒泡；工作区临时文件 finally 清理。"""
    video = await Video.filter(vid=vid).first()
    if not video or not video.transcript_segments:
        return
    work = storage.WORK_DIR / f'dub_{vid}'
    try:
        pairs = await translate_mod.translate_segments(video.transcript_segments, video.lang_source, lang_target)
        ass_path = storage.STORE_DIR / f'{vid}_ass.ass'
        ass_path.write_text(translate_mod.build_ass(pairs), encoding='utf-8')
        (storage.STORE_DIR / f'{vid}_sub.srt').write_text(translate_mod.build_srt(pairs), encoding='utf-8')

        work.mkdir(parents=True, exist_ok=True)
        # 逐句 TTS 自然语速，按各自 start 绝对时点叠加（不变速、不截断、空隙即静音）
        segs = []
        for n, p in enumerate(pairs):
            if not p['target']:
                continue
            seg_mp3 = work / f'{n:04d}.mp3'
            await translate_mod.tts_synth(p['target'], voice, seg_mp3)
            segs.append((seg_mp3, float(p['start'])))
        full_wav = work / 'dub.wav'
        total = float(video.duration or (pairs[-1]['end'] if pairs else 0))
        await translate_mod.mix_track(segs, full_wav, total)

        out = storage.STORE_DIR / f'{vid}_dub.mp4'
        await translate_mod.compose_video(storage.local_path(vid), ass_path, full_wav, out)
        # 产物落地才写元数据：dub_lang/dub_voice 恒等于当前 _dub.mp4 的配置
        video.dub_status = DubStatus.DONE.value
        video.dub_error = None
        video.dub_lang = lang_target
        video.dub_voice = voice
        await video.save(update_fields=['dub_status', 'dub_error', 'dub_lang', 'dub_voice'])
        logger.info('配音视频完成 %s（%d 句 / %s / %s）', vid, len(pairs), lang_target, voice)
    except (translate_mod.ProbeError, ValueError) as e:
        video.dub_status = DubStatus.FAILED.value
        video.dub_error = str(e)[:500]
        await video.save(update_fields=['dub_status', 'dub_error'])
        logger.warning('配音生成失败 %s：%s', vid, e)
    except Exception:
        logger.exception('配音生成异常 %s', vid)
        video.dub_status = DubStatus.FAILED.value
        video.dub_error = '配音生成失败，请稍后重试'
        await video.save(update_fields=['dub_status', 'dub_error'])
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def groups(user_id: str) -> list[dict]:
    """平台→账号分组树（单条 GROUP BY，只回计数，与记录总量无关）。

    返回 [{name, n, uploaders: [{name, n}]}]，按记录数倒序。空值归一为 ''（前端显「未归类」）。
    """
    vids = await _user_vids(user_id)
    rows = (
        await Video.filter(vid__in=vids)
        .annotate(n=Count('id'))
        .group_by('extractor', 'uploader')
        .values('extractor', 'uploader', 'n')
    )
    tree: dict[str, dict[str, int]] = {}
    for r in rows:
        p = r['extractor'] or ''
        u = r['uploader'] or ''
        tree.setdefault(p, {})
        tree[p][u] = tree[p].get(u, 0) + r['n']
    platforms = [
        {
            'name': p,
            'n': sum(ups.values()),
            'uploaders': sorted(({'name': u, 'n': n} for u, n in ups.items()), key=lambda x: -x['n']),
        }
        for p, ups in tree.items()
    ]
    platforms.sort(key=lambda x: -x['n'])
    return platforms


# 排序白名单：id 自增即入库顺序代理；按发布时间排时缺发布时间的统一垫底（_no_pub）
_HISTORY_SORTS = {
    'pub_desc': ('_no_pub', '-publish_at', '-id'),
    'pub_asc': ('_no_pub', 'publish_at', '-id'),
    'coll_desc': ('-id',),
    'coll_asc': ('id',),
}


async def history(
    user_id: str,
    *,
    page: int,
    page_size: int,
    status: Optional[str],
    platform: Optional[str] = None,
    uploader: Optional[str] = None,
    sort: str = 'pub_desc',
):
    """我的采集记录（UserVid 归属），分页 + 可排序。可按平台/账号过滤（空串=未归类组，None=不过滤）。

    sort 白名单（见 _HISTORY_SORTS），非法值回落 pub_desc（最新发布在前）。
    """
    vids = await _user_vids(user_id)
    q = Q(vid__in=vids)
    if status:
        q &= Q(status=status)
    if platform is not None:
        q &= (Q(extractor__isnull=True) | Q(extractor='')) if platform == '' else Q(extractor=platform)
    if uploader is not None:
        q &= (Q(uploader__isnull=True) | Q(uploader='')) if uploader == '' else Q(uploader=uploader)
    order = _HISTORY_SORTS.get(sort) or _HISTORY_SORTS['pub_desc']
    total = await Video.filter(q).count()
    query = Video.filter(q)
    if '_no_pub' in order:  # annotate 只挂列表查询；count 带注解易触发 Tortoise 怪异 SQL
        query = query.annotate(_no_pub=Case(When(publish_at__isnull=True, then=1), default=0))
    items = await query.offset((page - 1) * page_size).limit(page_size).order_by(*order)
    return total, items


async def remove(vid: str, user_id: str) -> bool:
    """删归属行；vid 最后一个引用消失才级联清库（文件/索引/块/库行，运行中先取消协程）。"""
    uv = await UserVid.filter(user_id=user_id, vid=vid).first()
    if not uv:
        return False
    await uv.delete()
    if await UserVid.filter(vid=vid).exists():
        return True  # 还有别的用户引用，全局库保留
    running = _running.get(vid)
    if running and not running.done():
        running.cancel()
    storage.delete(vid)
    await vectorstore.delete_vid(vid)  # 清 vec/fts 索引行（依赖主表查 id，须在删主表行之前）
    await VideoChunk.filter(vid=vid).delete()
    await Video.filter(vid=vid).delete()
    return True


async def stats(user_id: str) -> dict:
    """个人用量：今日/累计采集数、总字节、按站点分布（都基于自己的归属集合）。"""
    vids = await _user_vids(user_id)
    # 今日=业务时区自然日零点（UTC 零点会让早八前的采集不算"今日"）
    today_start = datetime.now(_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    total = len(vids)
    today = await Video.filter(vid__in=vids, create_at__gte=today_start).count()
    done = await Video.filter(vid__in=vids, status=VideoStatus.READY.value)
    total_bytes = sum(v.file_size or 0 for v in done)
    by_site: dict[str, int] = {}
    for v in done:
        site = v.extractor or 'unknown'
        by_site[site] = by_site.get(site, 0) + 1
    return {
        'total_videos': total,
        'today_videos': today,
        'total_bytes': total_bytes,
        'by_site': by_site,
        'quota_concurrent_downloads': settings.QUOTA_CONCURRENT_DOWNLOADS,
    }


# ============ 知识库（RAG）：检索 / 问答 / 回填索引（读路径在 core/rag.py） ============

# 运行中的补索引协程：user_id -> Task（防重复触发）
_kb_reindexing: dict[str, asyncio.Task] = {}


async def _kb_scope_vids(user_id: str, scope: Optional[dict]) -> Optional[list[str]]:
    """检索范围：vid（单视频）/ platform / uploader → vid 列表；None=不限（空列表=范围内无视频，调用方短路）。"""
    if not scope or not any(v is not None for v in scope.values()):
        return None
    vids = await _user_vids(user_id)
    if scope.get('vid'):  # 弹窗内单视频问答：锁死这一条（须属于该用户）
        return [scope['vid']] if scope['vid'] in vids else []
    q = Q(vid__in=vids)
    platform, uploader = scope.get('platform'), scope.get('uploader')
    if platform is not None:
        q &= (Q(extractor__isnull=True) | Q(extractor='')) if platform == '' else Q(extractor=platform)
    if uploader is not None:
        q &= (Q(uploader__isnull=True) | Q(uploader='')) if uploader == '' else Q(uploader=uploader)
    return await Video.filter(q).values_list('vid', flat=True)


async def kb_search(user_id: str, q: str, scope: Optional[dict], top_k: Optional[int]) -> list[dict]:
    vids = await _kb_scope_vids(user_id, scope)
    return await rag.search(user_id, q, top_k=top_k or settings.FINAL_TOPK, scope_vids=vids)


async def kb_ask(user_id: str, question: str, history_: Optional[list[dict]], scope: Optional[dict]) -> dict:
    vids = await _kb_scope_vids(user_id, scope)
    if vids is not None and not vids:
        return {'answer': '所选范围内还没有采集到视频。', 'rewritten': question.strip(), 'citations': []}
    return await rag.ask(user_id, question, history=history_, scope_vids=vids)


async def kb_status(user_id: str) -> dict:
    """索引进度：我的库里已转写的视频各状态计数（前端提示用）。"""
    vids = await _user_vids(user_id)
    qs = Video.filter(vid__in=vids).exclude(transcript=None)
    return {
        'total': await qs.count(),
        'indexed': await qs.filter(embed_status=EmbedStatus.DONE.value).count(),
        'running': await qs.filter(embed_status=EmbedStatus.RUNNING.value).count(),
        'failed': await qs.filter(embed_status=EmbedStatus.FAILED.value).count(),
    }


async def kb_reindex(user_id: str) -> int:
    """一键回填：我库里有转写但未索引（或 embedding 模型已换）的视频 → 串行补索引。返回排队数。"""
    task = _kb_reindexing.get(user_id)
    if task and not task.done():
        raise ValueError('补索引任务正在运行，请等待完成')

    vids = await _user_vids(user_id)
    stale = await (
        Video.filter(vid__in=vids, status=VideoStatus.READY.value)
        .exclude(transcript=None)
        .filter(Q(embed_status__in=[EmbedStatus.NONE.value, EmbedStatus.FAILED.value]))
        .values_list('vid', flat=True)
    )
    outdated = await VideoChunk.filter(vid__in=vids).exclude(model=settings.EMBED_MODEL).values_list('vid', flat=True)
    todo = list(dict.fromkeys([*stale, *sorted(set(outdated))]))
    if todo:
        _kb_reindexing[user_id] = asyncio.create_task(_kb_reindex_run(user_id, todo))
    return len(todo)


async def _kb_reindex_run(user_id: str, vids: list[str]):
    """串行补索引：逐条置 running 后跑 _embed，间隔 1s 防 embedding 限流。异常吞掉。"""
    try:
        for vid in vids:
            video = await Video.filter(vid=vid).first()
            if not video:
                continue  # 排队期间被删了
            video.embed_status = EmbedStatus.RUNNING.value
            video.embed_error = None
            await video.save(update_fields=['embed_status', 'embed_error'])
            await _embed(vid)
            await asyncio.sleep(1)
    except Exception:
        logger.exception('补索引任务异常')
    finally:
        _kb_reindexing.pop(user_id, None)  # 释放防重入槽位（漏了会永久占位，done 判断虽兜住但字典泄漏）


# ============ 订阅（方向二：频道 → 定时枚举 → 自动采集，全局共享） ============

_sub_task: Optional[asyncio.Task] = None
# 检查中的 sid：首检（后台）与手动「立即检查」并跑会让同一轮枚举重复处理，必须挡重入
_sub_checking: set[str] = set()


def sub_to_out(sub: Subscription) -> dict:
    return {
        'sid': sub.sid,
        'url': sub.url,
        'name': sub.name,
        'extractor': sub.extractor,
        'avatar': sub.avatar,
        'signature': sub.signature,
        'follower_count': sub.follower_count,
        'total_favorited': sub.total_favorited,
        'unique_id': sub.unique_id,
        'ip_location': sub.ip_location,
        'city': sub.city,
        'country': sub.country,
        'room_id': sub.room_id,
        'mplatform_followers_count': sub.mplatform_followers_count,
        'video_count': sub.video_count,
        # 检查进行中标记（进程内存态）：整轮要 1~3 分钟（探测退避），last_check_at 跑完才写，
        # 不透出这个前端就没法区分"还没开始"和"正在跑"
        'checking': sub.sid in _sub_checking,
        'last_check_at': sub.last_check_at.strftime(settings.DATETIME_FORMAT) if sub.last_check_at else None,
        'last_error': sub.last_error,
        'created_at': sub.create_at.strftime(settings.DATETIME_FORMAT) if sub.create_at else None,
    }


def sub_out(sub: Subscription, us: UserSub) -> dict:
    """Subscription 快照 + 该用户的个人偏好（check_hour/enum_limit），前端卡片统一结构。"""
    out = sub_to_out(sub)
    out['check_hour'] = us.check_hour
    out['enum_limit'] = us.enum_limit
    return out


async def list_subscriptions(user_id: str) -> list[dict]:
    """订阅列表：check_hour 是个人偏好（在 UserSub），这里 join 回来一起返回。
    collected_count = 当前用户已为该订阅采集到的视频条数（按 sid + user_id 统计 UserVid）。"""
    user_subs = await UserSub.filter(user_id=user_id).all()
    # 全局 avatar 映射（采集历史页用），空订阅时也要返回同构结构
    avatars = {
        name: avatar
        for name, avatar in await Subscription.filter(name__isnull=False, avatar__isnull=False).values_list(
            'name', 'avatar'
        )
    }
    if not user_subs:
        return {'subs': [], 'avatars': avatars}
    sids = [us.sid for us in user_subs]
    subs = await Subscription.filter(sid__in=sids).order_by('-create_at').all()
    sub_map = {s.sid: s for s in subs}
    # 批量查每个订阅下已采集条数：Subscription 全局共享，直接按 uploader 统计 Video 表
    counts = (
        await Video.filter(uploader__in=[sub.name for sub in subs])
        .annotate(count=Count('id'))
        .values('uploader', 'count')
    )
    count_map = {row['uploader']: row['count'] for row in counts}
    result = []
    for us in user_subs:
        sub = sub_map.get(us.sid)
        if not sub:
            continue
        out = sub_out(sub, us)
        out['collected_count'] = count_map.get(sub.name, 0)
        result.append(out)
    return {'subs': result, 'avatars': avatars}


async def create_subscription(user_id: str, url: str) -> tuple[Subscription, UserSub, dict]:
    """新增订阅：同步探测账号信息（失败快速报错）→ 全局 Subscription 复用或新建 → 挂 UserSub。

    探测只取账号基本信息（name/extractor/video_count），不自动采集视频；
    用户需在前端点击「立即采集」后才触发枚举+下载（走 _sub_check，自动与库中比对去重）。
    """
    url = urlguard.validate_media_url(url)
    if douyin.is_douyin_url(url) and not douyin.is_douyin_user_url(url):
        raise ValueError('抖音订阅请粘贴作者主页链接（douyin.com/user/xxx）')
    if bilibili.is_bilibili_url(url) and not bilibili.is_bilibili_user_url(url):
        raise ValueError('B站订阅请粘贴空间页链接（space.bilibili.com/xxx）')
    sub = await Subscription.filter(url=url).first()
    if sub and await UserSub.filter(user_id=user_id, sid=sub.sid).exists():
        raise ValueError('你已订阅过该频道')
    info = await _sub_probe(url)  # 轻量探测：只取账号基本信息，不拉视频列表
    if not sub:
        try:
            sub = await Subscription.create(
                url=url,
                name=info.get('name'),
                extractor=info.get('extractor'),
                avatar=info.get('avatar'),
                signature=info.get('signature'),
                follower_count=info.get('follower_count'),
                total_favorited=info.get('total_favorited'),
                unique_id=info.get('unique_id'),
                ip_location=info.get('ip_location'),
                city=info.get('city'),
                country=info.get('country'),
                room_id=info.get('room_id'),
                mplatform_followers_count=info.get('mplatform_followers_count'),
                video_count=info.get('total'),
            )
        except IntegrityError:  # 并发竞态：他人刚建同频道 → 复用
            sub = await Subscription.filter(url=url).first()
            if not sub:
                raise ValueError('订阅冲突，请重试')
    us = await UserSub.get_or_create(user_id=user_id, sid=sub.sid)
    return sub, us[0], info


async def _sub_authorized(user_id: str, sid: str) -> tuple[Subscription, UserSub]:
    """校验该用户确实订阅了 sid，返回 (Subscription, UserSub) 两行；无权限抛 ValueError。

    一并返回 UserSub 行，调用方要读/写 check_hour/enum_limit 无需再查一次。
    """
    sub = await Subscription.filter(sid=sid).first()
    us = await UserSub.filter(user_id=user_id, sid=sid).first()
    if not sub or not us:
        raise ValueError('订阅不存在')
    return sub, us


async def remove_subscription(user_id: str, sid: str) -> bool:
    """退订只删自己的 UserSub 行；无人订阅后回收 Subscription（已入库视频不受影响）。"""
    us = await UserSub.filter(user_id=user_id, sid=sid).first()
    if not us:
        return False
    background.unregister_sub_job(user_id, sid)
    await us.delete()
    if not await UserSub.filter(sid=sid).exists():
        await Subscription.filter(sid=sid).delete()
    return True


async def reprobe_subscription(user_id: str, sid: str) -> dict:
    """重新探测账号基本信息（头像/简介/粉丝等），更新 Subscription 表后返回最新快照。"""
    sub, _ = await _sub_authorized(user_id, sid)
    info = await _sub_probe(sub.url)
    await Subscription.filter(sid=sid).update(
        name=info.get('name') or sub.name,
        extractor=info.get('extractor') or sub.extractor,
        avatar=info.get('avatar'),
        signature=info.get('signature'),
        follower_count=info.get('follower_count'),
        total_favorited=info.get('total_favorited'),
        unique_id=info.get('unique_id'),
        ip_location=info.get('ip_location'),
        city=info.get('city'),
        country=info.get('country'),
        room_id=info.get('room_id'),
        mplatform_followers_count=info.get('mplatform_followers_count'),
        video_count=info.get('total'),
    )
    sub = await Subscription.filter(sid=sid).first()
    return {'sub': sub_to_out(sub)}


async def _sub_check(sid: str, limit: Optional[int] = None) -> dict:
    """检查一个订阅：枚举最新 → 新条目入库一次 → 挂给所有订阅者。返回 {checked, queued}。

    失败记录不自动重试是有意为之：图集/超长这类失败重试只会反复撞风控；想救去采集记录页手动重试。
    limit：本轮枚举最新条数，缺省用后端默认 SUB_ENUM_LIMIT。
    """
    sub = await Subscription.filter(sid=sid).first()
    if not sub:
        raise ValueError('订阅不存在')
    if sid in _sub_checking:
        raise ValueError('该订阅正在检查中，请等本轮完成（探测退避时可能要几分钟）')
    _sub_checking.add(sid)
    try:
        return await _sub_run(sub, await _sub_enum_url(sub.url, limit))
    except (ytdlp.ProbeError, ValueError) as e:
        sub.last_check_at = datetime.now(timezone.utc)
        sub.last_error = str(e)[:500]
        await sub.save(update_fields=['last_check_at', 'last_error'])
        raise
    finally:
        _sub_checking.discard(sid)


async def _sub_run(sub: Subscription, enum: dict) -> dict:
    """拿着枚举结果采集新条目 + 回写检查状态。调用方必须已占住 _sub_checking。"""
    queued = await _sub_collect(sub.sid, enum['items'])
    sub.last_check_at = datetime.now(timezone.utc)
    sub.last_error = None
    sub.name = sub.name or enum.get('name')
    sub.extractor = sub.extractor or enum.get('extractor')
    if enum.get('avatar'):
        sub.avatar = enum['avatar']
    if enum.get('total') is not None:
        sub.video_count = enum['total']
    await sub.save(update_fields=['last_check_at', 'last_error', 'name', 'extractor', 'avatar', 'video_count'])
    if queued:
        logger.info('订阅 %s 本轮新增挂接 %d 条', sub.sid, queued)
    return {'checked': len(enum['items']), 'queued': queued}


async def _sub_collect(sid: str, items: list[dict]) -> int:
    """枚举结果入库：新视频全局下载一次，挂给该订阅所有还缺它的用户。返回挂接条数。"""
    users = list(await UserSub.filter(sid=sid).values_list('user_id', flat=True))
    if not users:
        return 0
    ids = [i['id'] for i in items if i.get('id')]
    in_lib = set(await Video.filter(vid__in=ids).values_list('vid', flat=True)) if ids else set()
    have: dict[str, set] = {u: set() for u in users}
    if ids:
        for row in await UserVid.filter(user_id__in=users, vid__in=ids).values('user_id', 'vid'):
            have[row['user_id']].add(row['vid'])
    queued = 0
    for i in items:
        pid = i.get('id')
        if not pid:
            logger.warning('订阅 %s 跳过无 ID 条目：%s', sid, i.get('url'))
            continue
        missing = [u for u in users if pid not in have[u]]
        if not missing:
            logger.info('订阅 %s 跳过已采 vid=%s：%s', sid, pid, i.get('title') or i.get('url'))
            continue
        try:
            if pid not in in_lib:  # 库里没有 → 下载一次（系统任务不占个人配额）
                await collect(
                    user_id=missing[0], url=i['url'], check_quota=False, download_url=i.get('play_url'), info=i
                )
                in_lib.add(pid)
            for u in missing:  # 库里已有 → 纯挂接，零下载
                await UserVid.get_or_create(user_id=u, vid=pid)
                have[u].add(pid)
            queued += 1
        except (ytdlp.ProbeError, ValueError) as e:
            logger.warning('订阅 %s 自动采集失败 %s：%s', sid, i.get('url'), e)
        await asyncio.sleep(settings.PROBE_RETRY_INTERVAL)  # 连发限速
    return queued


async def _sub_probe(url: str) -> dict:
    """订阅时轻量探测账号基本信息（不拉视频列表），按域名分流。"""
    if douyin.is_douyin_url(url):
        return await douyin.probe_user(url)
    if bilibili.is_bilibili_url(url):
        return await bilibili.probe_user(url)
    # yt-dlp 等：probe 只取单个视频的元信息，对频道主页链接也适用
    items = await ytdlp.probe(url)
    item = items[0] if items else {}
    return {
        'name': item.get('uploader'),
        'extractor': item.get('extractor'),
        'avatar': item.get('thumbnail'),
        'total': None,  # yt-dlp probe 不返回频道视频总数
        'signature': None,
        'follower_count': None,
        'total_favorited': None,
        'unique_id': None,
        'ip_location': None,
        'city': None,
        'country': None,
        'room_id': None,
        'mplatform_followers_count': None,
    }


async def _sub_enum_url(url: str, limit: Optional[int] = None) -> dict:
    """按域名分流枚举渠道，带瞬态重试（风控/频率限制）。limit 缺省用后端默认 SUB_ENUM_LIMIT。"""
    n = limit or settings.SUB_ENUM_LIMIT
    for attempt in range(1, settings.PROBE_ATTEMPTS + 1):
        try:
            if douyin.is_douyin_url(url):
                return await douyin.list_user_latest(url, n)
            if bilibili.is_bilibili_url(url):
                return await bilibili.list_user_latest(url, n)
            return await ytdlp.list_latest(url, n)
        except ytdlp.ProbeError as e:
            if not _is_transient(e) or attempt == settings.PROBE_ATTEMPTS:
                raise
            logger.warning('订阅枚举瞬态失败（%d/%d）%s：%s', attempt, settings.PROBE_ATTEMPTS, url, e)
            await asyncio.sleep(settings.PROBE_RETRY_INTERVAL * attempt)


async def _sub_check_bg(sid: str, limit: Optional[int] = None):
    """后台检查（手动「检查」用）：吞异常——_sub_check 内部已落 last_error + 日志。"""
    try:
        await _sub_check(sid, limit)
    except Exception:
        pass


async def set_subscription_settings(user_id: str, sid: str, hour: Optional[int], limit: int) -> dict:
    """设置订阅偏好（写 UserSub）：每日定点检查小时（null=不自动检查）+ 每轮枚举条数。

    同步 APScheduler：设了定点注册 cron job，设 null 移除。
    """
    if hour is not None and not 0 <= hour <= 23:
        raise ValueError('定点小时须在 0-23 之间')
    if not 1 <= limit <= 50:
        raise ValueError('条数须在 1-50 之间')
    sub, us = await _sub_authorized(user_id, sid)
    us.check_hour = hour
    us.enum_limit = limit
    await us.save(update_fields=['check_hour', 'enum_limit'])
    if hour is not None:
        background.register_sub_job(user_id, sid, hour)
    else:
        background.unregister_sub_job(user_id, sid)
    return sub_out(sub, us)


async def start_subscription_check(user_id: str, sid: str) -> None:
    """手动检查：校验后扔后台立即返回。

    探测退避重试一轮可能几分钟，不能挂住 HTTP 请求（前端 axios 60s 超时会误报网络异常）；
    重入由 _sub_checking 挡（权威闸门），前端重复点击会拿到「正在检查中」。
    枚举条数读该用户 UserSub.enum_limit（与定时检查共享同一设置）。
    """
    _, us = await _sub_authorized(user_id, sid)
    if sid in _sub_checking:
        raise ValueError('该订阅正在检查中，请等本轮完成')
    asyncio.create_task(_sub_check_bg(sid, us.enum_limit))
