import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# 获取当前脚本所在目录的绝对路径（app/core）
current_dir = Path(__file__).resolve().parent
# 获取 app 目录
BASE_DIR = current_dir.parent
# 定义 .env 文件路径（app/.env）
ENV_FILE = BASE_DIR / '.env'
# 手动加载 .env 文件（run.sh 已 source 一遍，这里兜底直接 python main.py 的场景）
load_dotenv(ENV_FILE)


class Settings(BaseSettings):
    # 应用元信息（API 文档）
    APP_TITLE: str = 'NexaClip'
    APP_DESCRIPTION: str = '云原生音视频下载服务：FastAPI + yt-dlp + FFmpeg，成品本地永久留存'
    VERSION: str = '0.1.0'

    BACK_END_PORT: int = 7860

    # 数据目录：db / work / store / logs 都在这下面（独立于 TORTOISE_ORM，避免从连接字典反推路径）
    DATA_DIR: str = f'{BASE_DIR}/data'

    # 数据库：DB_TYPE 选连接后端（验证期 sqlite，长期留存改 postgres）。
    # ⚠️ 仅 ORM 表（用户/视频/归属/订阅）改 postgres 零代码改动；语义检索层（向量 + 全文）
    #    目前是 SQLite 专属（core/vectorstore.py 用 sqlite-vec + FTS5），切 postgres 前须先补
    #    pgvector 实现，否则 vec_chunk/video_chunk_fts 虚表不建，_embed 与 rag 检索会报错。
    # 各连接的具体参数在下方 TORTOISE_ORM 里用 os.getenv 直接读，不再重复声明字段
    DB_TYPE: str = os.getenv('DB_TYPE', 'sqlite')

    # JWT（生产环境务必换随机长串，且固定写死在 Secret——随机默认值会令每次重启所有 token 失效）
    JWT_SECRET_KEY: str = os.getenv('JWT_SECRET_KEY', 'secret_key')
    JWT_ALGORITHM: str = 'HS256'
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12  # access token 12 小时
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30  # refresh token 30 天

    # 前端地址（重置密码邮件里的链接前缀）
    USER_FE_URL: str = os.getenv('USER_FE_URL', 'http://localhost:5173')

    # 默认管理员（首次启动自动创建；留空则不创建——不内置弱默认凭据）
    SUPER_ADMIN_EMAIL: str = os.getenv('SUPER_ADMIN_EMAIL', '')
    SUPER_ADMIN_NAME: str = os.getenv('SUPER_ADMIN_NAME', 'admin')
    SUPER_ADMIN_PASSWORD: str = os.getenv('SUPER_ADMIN_PASSWORD', '')

    # 邮箱服务（注册验证码 / 找回密码）
    EMAIL_HOST: str = os.getenv('EMAIL_HOST', '')
    EMAIL_PORT: int = os.getenv('EMAIL_PORT', 465)
    EMAIL_USER: str = os.getenv('EMAIL_USER', '')
    EMAIL_PASSWORD: str = os.getenv('EMAIL_PASSWORD', '')
    EMAIL_SENDER: str = os.getenv('EMAIL_SENDER', '')
    LOGO_NAME: str = os.getenv('LOGO_NAME', 'NexaClip')

    # ============ 下载引擎与配额 ============
    # cookies 一律用浏览器插件导出的 Netscape txt 文件；env 只存文件路径，默认 data/cookies/www.<站点>.com_cookies.txt，把文件放到位即可
    YOUTUBE_COOKIES: str = os.getenv('YOUTUBE_COOKIES', f'{DATA_DIR}/cookies/www.youtube.com_cookies.txt')
    DOUYIN_COOKIES: str = os.getenv('DOUYIN_COOKIES', f'{DATA_DIR}/cookies/www.douyin.com_cookies.txt')
    BILIBILI_COOKIES: str = os.getenv('BILIBILI_COOKIES', f'{DATA_DIR}/cookies/www.bilibili.com_cookies.txt')
    HTTP_PROXY_URL: str = os.getenv('HTTP_PROXY_URL', '')
    DOWNLOAD_CONCURRENCY: int = 2  # 全局并发下载数（免费档 2 vCPU）
    DOWNLOAD_TIMEOUT: int = 1800  # 单任务下载超时（秒）
    # 探测瞬态失败（风控/频率限制）重试：最多尝试次数、基础间隔（间隔按次数线性递增）
    PROBE_ATTEMPTS: int = 5
    PROBE_RETRY_INTERVAL: int = 2
    # 探测阶段即拒：时长上限（秒）、预估体积上限（字节）、是否允许直播
    MAX_DURATION_SECONDS: int = 3600
    MAX_FILE_SIZE: int = 500 * 1024 * 1024
    ALLOW_LIVE: bool = False
    QUOTA_CONCURRENT_DOWNLOADS: int = 2  # 用户同时进行下载数上限

    # ============ AI 原子能力（千流云 Qianliu：一把 QL_KEY 通用 chat/embedding/rerank/asr/tts） ============
    # 控制台创建 key：https://console.qlcloud.top/keys 。网关 base 与模型档位写死在此，.env 只填 QL_KEY。
    QL_KEY: str = os.getenv('QL_KEY', '')

    # 对话 / 向量 / 重排：共用 OpenAI 兼容网关 /gateway/llm/v1
    LLM_URL: str = 'https://api.qlcloud.top/gateway/llm/v1'
    LLM_MODEL: str = 'deepseek-v4.1-flash'  # /chat/completions
    EMBED_MODEL: str = 'bge-m3'  # /embeddings，1024 维（与 vectorstore._DIM 一致）
    RERANK_MODEL: str = 'bge-reranker-v2-m3'  # /rerank（软依赖，失败降级）

    # 文案转写：独立网关 /gateway/asr/v1。asr-pro + verbose_json 返回句级 segments（时间轴命脉）
    ASR_URL: str = 'https://api.qlcloud.top/gateway/asr/v1'
    ASR_MODEL: str = 'asr-pro'
    ASR_TIMEOUT: int = 900  # 单次转写超时（秒）：整段 60 分钟音频服务端需要数分钟

    # 配音合成：独立网关 /gateway/tts/v1。tts-flash 档（音色见 translate.LANGS）
    TTS_URL: str = 'https://api.qlcloud.top/gateway/tts/v1'
    TTS_MODEL: str = 'tts-flash'

    # ============ 知识库（RAG：向量索引 + 混合检索 + 问答） ============
    EMBED_BATCH: int = 32  # embedding 单批块数
    RECALL_TOPK: int = 30  # 向量/关键词单路召回候选数
    FUSE_TOPK: int = 20  # RRF 融合后进重排候选数
    FINAL_TOPK: int = 8  # 最终返回给前端/LLM 的命中数
    RRF_K: int = 60  # RRF 阻尼常数

    # ============ 订阅（方向二：频道新视频自动采集） ============
    TIMEZONE: str = 'Asia/Shanghai'  # 业务时区：订阅定点检查按它算（Tortoise timezone 同源）
    SUB_ENUM_LIMIT: int = int(os.getenv('SUB_ENUM_LIMIT', '10'))  # 每次枚举的最新条数（测试可设 2 加速）

    # 数据库配置
    # 只列已装驱动的两种（aiosqlite + asyncpg，见 pyproject tortoise-orm[asyncpg]）；
    # Tortoise 启动会 import 每个声明连接的引擎模块，加了没装驱动的 mysql 会直接崩，故删。
    # default_connection 由 DB_TYPE 选。切 postgres 对 ORM 表是纯环境变量、零代码改动；
    # 但语义检索层（core/vectorstore.py）当前仅支持 sqlite-vec + FTS5，需另实现 pgvector 才可用。
    TORTOISE_ORM: dict = {
        'connections': {
            'sqlite': {
                'engine': 'tortoise.backends.sqlite',
                'credentials': {'file_path': f'{DATA_DIR}/db.sqlite3'},
            },
            'postgres': {
                'engine': 'tortoise.backends.asyncpg',
                'credentials': {
                    'host': os.getenv('PG_HOST', 'localhost'),
                    'port': int(os.getenv('PG_PORT', 5432)),
                    'user': os.getenv('PG_USER', 'root'),
                    'password': os.getenv('PG_PASSWORD', 'root'),
                    'database': os.getenv('PG_DATABASE', 'nexaclip'),
                },
            },
        },
        'apps': {
            'models': {
                'models': ['models', 'aerich.models'],
                'default_connection': os.getenv('DB_TYPE', 'sqlite'),
            },
        },
        'use_tz': True,
        'timezone': TIMEZONE,
    }
    DATETIME_FORMAT: str = '%Y-%m-%d %H:%M:%S'

    model_config = SettingsConfigDict(case_sensitive=True)


settings = Settings()
