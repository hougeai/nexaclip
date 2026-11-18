from typing import List, Optional

from pydantic import BaseModel, Field


# ============ 探测/采集 ============
class ProbeReq(BaseModel):
    urls: List[str] = Field(
        ..., description='待采集链接（批量，服务端并发；探测即自动下载）', min_length=1, max_length=20
    )


# ============ 视频元信息编辑 ============
class VideoMetaReq(BaseModel):
    title: Optional[str] = Field(None, max_length=500, description='视频标题；None=不改')
    uploader: Optional[str] = Field(None, max_length=200, description='上传者/账号名（影响分组）；None=不改')


# ============ 时间轴高光 ============
class HighlightPoint(BaseModel):
    start: float = Field(..., ge=0, description='起始秒')
    end: float = Field(..., gt=0, description='结束秒')


class HighlightClipReq(BaseModel):
    points: List[HighlightPoint] = Field(..., min_length=1, max_length=20, description='选中的高亮区间')
    merge: bool = Field(False, description='true=按时间顺序拼成一个高光集；false=每段一个文件')


# ============ 翻译 / 配音（源语言转写时自动检测，用户只选目标语言 + 音色） ============
class TranslateReq(BaseModel):
    lang_target: str = Field(..., min_length=2, max_length=2, description='目标语言：zh/en')
    voice: Optional[str] = Field(
        None, max_length=50, description='千流云 tts-flash 音色 ID（仅配音用）；None=该语种默认女声'
    )


# ============ 订阅（方向二：频道自动采集） ============
class SubCreateReq(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048, description='频道/作者主页链接（抖音为 /user/xxx 主页）')


class SubSettingsReq(BaseModel):
    """订阅的两项个人偏好（都在 UserSub），一次提交。"""

    hour: Optional[int] = Field(None, ge=0, le=23, description='每日定点检查小时（东八区）；null=不自动检查')
    limit: int = Field(..., ge=1, le=50, description='每轮枚举最新条数（定时+手动共享）')


# ============ 知识库（RAG） ============
class KbScope(BaseModel):
    platform: Optional[str] = Field(None, description='平台(extractor)；空串=未归类，缺省=不限')
    uploader: Optional[str] = Field(None, description='账号(uploader)；空串=未归类，缺省=不限')
    vid: Optional[str] = Field(
        None, max_length=100, description='限定单视频（弹窗内问答用）；给定时忽略 platform/uploader'
    )


class KbSearchReq(BaseModel):
    q: str = Field(..., min_length=1, max_length=200, description='检索词')
    scope: Optional[KbScope] = None
    top_k: Optional[int] = Field(None, ge=1, le=30, description='返回条数（默认 FINAL_TOPK）')


class KbAskTurn(BaseModel):
    q: str = Field(..., max_length=500)
    a: str = Field(..., max_length=4000)


class KbAskReq(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    history: List[KbAskTurn] = Field(default_factory=list, max_length=6, description='最近问答轮（指代消解用）')
    scope: Optional[KbScope] = None
