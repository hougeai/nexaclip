from typing import Optional

from tortoise import fields

from core.config import settings
from core.idgen import gen_id
from models.enums import AiStatus, DubStatus, EmbedStatus, HighlightStatus, VideoStatus

from .base import BaseModel, TimestampMixin


class Video(BaseModel, TimestampMixin):
    """全局媒体库：一个平台视频一行——一份文件、一份转写、一份报告、一份向量。

    vid = 平台内视频 ID（抖音 aweme_id / yt-dlp id），全局唯一；
    用户归属走 UserVid 关联表：N 个用户采同一视频只下载一次，删除按引用计数回收。
    """

    # 标识（全局唯一）与来源
    vid = fields.CharField(max_length=100, unique=True, pk=False, description='平台内视频 ID（全局唯一）', index=True)
    source_url = fields.CharField(max_length=2048, description='规范化后的视频链接（同链接再提交免探测）', index=True)

    # 探测快照（原站删了记录也能看）
    title = fields.CharField(max_length=500, null=True, description='标题')
    uploader = fields.CharField(max_length=200, null=True, description='上传者')
    duration = fields.IntField(null=True, description='时长（秒）')
    thumbnail = fields.CharField(max_length=1024, null=True, description='封面 URL')
    extractor = fields.CharField(max_length=100, null=True, description='来源站点')
    channel = fields.CharField(max_length=20, null=True, description='下载渠道：douyin / bilibili / ytdlp')
    publish_at = fields.BigIntField(null=True, description='原站发布时间（UTC 秒）')

    # 下载状态机
    status = fields.CharField(max_length=20, default=VideoStatus.QUEUED.value, description='状态', index=True)
    percent = fields.FloatField(null=True, description='进度百分比')
    error_msg = fields.CharField(max_length=500, null=True, description='归一化错误文案')
    started_at = fields.DatetimeField(null=True, description='开始下载时间')
    finished_at = fields.DatetimeField(null=True, description='结束时间（ready/failed）')

    # 产物（成品文件按 store/{vid}.ext 存放，local_path 精确定位）
    file_name = fields.CharField(max_length=500, null=True, description='展示文件名')
    file_size = fields.BigIntField(null=True, description='成品字节数')

    # 文案转写（下载完成后异步提取）：transcript 与 error 双空 = 提取中/未开启
    transcript = fields.TextField(null=True, description='视频文案（ASR 转写）')
    transcript_segments = fields.JSONField(null=True, description='分段时间戳：[{start,end,text}]')
    transcript_error = fields.CharField(max_length=500, null=True, description='转写失败原因')
    lang_source = fields.CharField(
        max_length=10, null=True, description='文案源语言（转写完成后自动检测：zh/en/ja/ko；null=判不出）'
    )

    # AI 报告（转写完成后异步生成）：大纲章节 + 图文报告
    ai_status = fields.CharField(max_length=20, default=AiStatus.NONE.value, description='AI 报告状态')
    ai_chapters = fields.JSONField(null=True, description='章节：[{title,start,end,summary}]')
    ai_report = fields.TextField(null=True, description='AI 图文报告（Markdown）')
    ai_error = fields.CharField(max_length=500, null=True, description='AI 分析失败原因')

    # 向量索引（转写完成后异步生成）：全库检索/问答用，vec/fts 虚表 rowid 对齐 video_chunk.id
    embed_status = fields.CharField(max_length=20, default=EmbedStatus.NONE.value, description='向量索引状态')
    embed_error = fields.CharField(max_length=500, null=True, description='索引失败原因')

    # 时间轴高光（用户手动触发，不自动跑）：与 ai_* 三件套同构，start/end 取自转写分段时间戳
    highlight_status = fields.CharField(max_length=20, default=HighlightStatus.NONE.value, description='高光标注状态')
    highlights = fields.JSONField(
        null=True, description='高亮点：[{start,end,type,label,reason}]，type=quote/conflict/concept/turn'
    )
    highlight_error = fields.CharField(max_length=500, null=True, description='高光标注失败原因')

    # 视频翻译配音（用户手动触发）：srt/ass 同步秒级无需状态；配音视频异步走 dub_status。
    # dub_lang/dub_voice 是产物元数据：done 时写入，读表即知当前 {vid}_dub.mp4 是哪语言哪音色
    dub_status = fields.CharField(max_length=20, default=DubStatus.NONE.value, description='配音视频生成状态')
    dub_error = fields.CharField(max_length=500, null=True, description='配音生成失败原因')
    dub_lang = fields.CharField(max_length=10, null=True, description='配音目标语言（产物元数据）')
    dub_voice = fields.CharField(max_length=50, null=True, description='配音音色 ID（产物元数据）')

    class Meta:
        table = 'video'
        # 字段级索引已够：vid(unique) / source_url / status。用户视角查询全部由 UserVid 的 vid 集合驱动，
        # 旧 (user_id,*) 与 (extractor,uploader) 联合索引在共享库重构后失去用武之地——真出现全库按平台扫描再加。

    @classmethod
    async def create_from_info(cls, source_url: str, info: dict, channel: str, *, vid: Optional[str] = None) -> 'Video':
        """探测结果落库为新库条目。vid 用探测出的平台视频 ID（短链/全链归一化去重）；缺失时兜底生成短 ID。"""
        rec = cls(
            vid=vid or info.get('id') or gen_id(12),
            source_url=source_url,
            title=info.get('title'),
            uploader=info.get('uploader'),
            duration=int(info['duration']) if info.get('duration') else None,
            thumbnail=info.get('thumbnail'),
            extractor=info.get('extractor'),
            channel=channel,
            publish_at=info.get('publish_at'),
            status=VideoStatus.QUEUED.value,
        )
        await rec.save()
        return rec


class UserVid(BaseModel, TimestampMixin):
    """用户采集记录（Video 关联表）：删除只删本行；vid 最后一个引用消失时级联清库（文件/转写/索引）。"""

    user_id = fields.CharField(max_length=12, description='所属用户', index=True)
    vid = fields.CharField(max_length=100, description='视频（平台 ID）', index=True)

    class Meta:
        table = 'user_vid'
        unique_together = (('user_id', 'vid'),)


class VideoChunk(BaseModel, TimestampMixin):
    """转写切出的语义块：知识库检索/问答最小单元，全员共享（归属过滤在检索层按用户 vid 集合做）。

    文本与时间戳的唯一事实源；向量存 vec_chunk 虚表、全文存 video_chunk_fts 虚表，
    三处 id/rowid 对齐，由 core/vectorstore.py 显式同步（Tortoise 不管虚表）。
    """

    vid = fields.CharField(max_length=100, description='归属视频（平台视频 ID）', index=True)
    seq = fields.IntField(description='块序号（0 起）')
    text = fields.TextField(description='块正文（≤200 字）')
    start = fields.FloatField(description='块起始秒')
    end = fields.FloatField(description='块结束秒')
    model = fields.CharField(max_length=100, description='embedding 模型指纹（换模型时校验重建）')

    class Meta:
        table = 'video_chunk'
        indexes = (('vid', 'seq'),)


class Subscription(BaseModel, TimestampMixin):
    """订阅源（全局共享）：一个频道/作者主页一行，轮询成果挂给所有订阅者（UserSub）。

    调度为单常驻协程表驱动（无每订阅任务、无内存注册表），重启即自愈；
    name/extractor/avatar/signature 等账号信息创建时同步探测（探测失败快速报错不落库），每次检查刷新。
    last_check_at/last_error 是全局检查状态（每次检查更新，所有订阅者共用）。
    check_hour 是个人偏好，存在 UserSub（不同用户可对同一频道设不同状态）。
    """

    sid = fields.CharField(max_length=12, unique=True, pk=False, description='订阅标识', index=True)
    url = fields.CharField(max_length=2048, unique=True, description='频道/作者主页链接（全局唯一）')
    name = fields.CharField(max_length=200, null=True, description='频道名（创建时同步探测）')
    extractor = fields.CharField(max_length=100, null=True, description='平台（创建时同步探测）')
    avatar = fields.CharField(max_length=2048, null=True, description='频道头像 URL（创建时同步探测）')
    signature = fields.CharField(max_length=500, null=True, description='账号简介（创建时同步探测）')
    follower_count = fields.BigIntField(null=True, description='粉丝数（创建时同步探测；部分平台拿不到，null 正常）')
    total_favorited = fields.BigIntField(
        null=True, description='账号视频被赞总数（创建时同步探测；部分平台拿不到，null 正常）'
    )
    unique_id = fields.CharField(
        max_length=100, null=True, description='抖音号（创建时同步探测；部分平台拿不到，null 正常）'
    )
    ip_location = fields.CharField(
        max_length=100, null=True, description='IP 属地（创建时同步探测；部分平台拿不到，null 正常）'
    )
    city = fields.CharField(max_length=100, null=True, description='城市（创建时同步探测；部分平台拿不到，null 正常）')
    country = fields.CharField(
        max_length=100, null=True, description='国家（创建时同步探测；部分平台拿不到，null 正常）'
    )
    room_id = fields.CharField(
        max_length=50, null=True, description='直播间 ID（创建时同步探测；部分平台拿不到，null 正常）'
    )
    mplatform_followers_count = fields.BigIntField(
        null=True, description='多平台粉丝数（创建时同步探测；部分平台拿不到，null 正常）'
    )
    video_count = fields.BigIntField(null=True, description='频道视频总数（每次检查刷新；部分平台拿不到，null 正常）')
    last_check_at = fields.DatetimeField(null=True, description='最近一次检查时间（全局，所有订阅者共用）')
    last_error = fields.CharField(max_length=500, null=True, description='最近一次检查失败原因（全局）')

    async def save(self, *args, **kwargs):
        if not self.sid:
            self.sid = gen_id(12)
        return await super().save(*args, **kwargs)


class UserSub(BaseModel, TimestampMixin):
    """用户订阅关系（Subscription 关联表）：删订阅只删本行；无订阅者后 Subscription 由检查/清理回收。

    check_hour 是个人偏好（每个用户可设不同的检查时间），存在本表而非全局 Subscription。
    null=不自动检查，仅手动「立即采集」触发；非 null=每天该小时自动检查一次。
    """

    user_id = fields.CharField(max_length=12, description='所属用户', index=True)
    sid = fields.CharField(max_length=12, description='订阅标识', index=True)
    check_hour = fields.IntField(null=True, description='每日定点检查小时 0-23（TIMEZONE 时区）；null=不自动检查')
    enum_limit = fields.IntField(
        default=settings.SUB_ENUM_LIMIT, description='每轮枚举最新条数（定时+手动共享），默认取环境变量 SUB_ENUM_LIMIT'
    )

    class Meta:
        table = 'user_sub'
        unique_together = (('user_id', 'sid'),)
