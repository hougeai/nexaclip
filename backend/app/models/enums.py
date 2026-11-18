"""采集/下载状态枚举。

DB 层用 CharField 存字符串值（对齐项目风格：状态字段不用 CharEnumField），
业务层引用这里的 Enum 做类型约束，避免魔法字符串。
"""

from enum import Enum


class Channel(str, Enum):
    """下载渠道：按链接域名分流，落库记录用哪条链路取流。"""

    DOUYIN = 'douyin'  # 抖音：f2 + a_bogus 签名
    BILIBILI = 'bilibili'  # B站：公开 API 直调（不走 yt-dlp 网页抓取，412）
    YTDLP = 'ytdlp'  # 其余站点：yt-dlp 内置 extractor
    UPLOAD = 'upload'  # 本地上传：无取流环节，文件直落 store 走转写/报告/索引（覆盖无法下载的视频）


class AiStatus(str, Enum):
    """AI 报告生成状态（转写完成后异步触发，独立于下载状态机）。"""

    NONE = 'none'  # 未开始
    RUNNING = 'running'  # 生成中
    DONE = 'done'  # 已完成
    FAILED = 'failed'  # 失败（ai_error 存归一化文案）


class EmbedStatus(str, Enum):
    """向量索引状态（转写完成后异步触发，与 AI 报告同构）。"""

    NONE = 'none'  # 未索引
    RUNNING = 'running'  # 索引中
    DONE = 'done'  # 已索引，可检索
    FAILED = 'failed'  # 失败（embed_error 存归一化文案）


class HighlightStatus(str, Enum):
    """时间轴高光标注状态（用户手动触发，报告完成后才值得标，故不自动跑）。"""

    NONE = 'none'  # 未标注
    RUNNING = 'running'  # 标注中
    DONE = 'done'  # 已标注
    FAILED = 'failed'  # 失败（highlight_error 存归一化文案）


class DubStatus(str, Enum):
    """配音视频生成状态（用户手动触发翻译配音，异步管线）。"""

    NONE = 'none'  # 未生成
    RUNNING = 'running'  # 生成中
    DONE = 'done'  # 已生成（store/{vid}_dub.mp4）
    FAILED = 'failed'  # 失败（dub_error 存归一化文案）


class HighlightType(str, Enum):
    """高亮点类型：前端按类型着色，LLM 只能取这四个值。"""

    QUOTE = 'quote'  # 金句
    CONFLICT = 'conflict'  # 冲突/争议
    CONCEPT = 'concept'  # 核心概念
    TURN = 'turn'  # 转折


class VideoStatus(str, Enum):
    """一条采集记录的状态机：probe 落库即排队，随后自动下载，成品就绪可取文件。

    前端展示文案：queued=采集完成、downloading=视频下载中、ready=下载完成。
    """

    QUEUED = 'queued'  # 采集完成，排队等待下载
    DOWNLOADING = 'downloading'  # 视频下载中
    READY = 'ready'  # 下载完成，可保存到本地
    FAILED = 'failed'  # 失败（error_msg 存归一化文案）
