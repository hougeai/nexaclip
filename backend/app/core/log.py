import os
import logging
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from zoneinfo import ZoneInfo

from .config import settings


class _TzFormatter(logging.Formatter):
    """把 asctime 强制转成 settings.TIMEZONE，避免容器 TZ=UTC 时日志时间与业务时区对不上。"""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=ZoneInfo(settings.TIMEZONE))
        return dt.strftime('%Y-%m-%d %H:%M:%S')


def get_logger(log_name='app.log'):
    logger_name = log_name.replace('.log', '')  # 具名 logger：如 "app", "ota"
    logger = logging.getLogger(logger_name)
    if logger.hasHandlers():
        return logger

    logger.setLevel(logging.INFO)

    # 创建日志目录
    log_dir = os.path.join(os.path.dirname(__file__), '../data/logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, log_name)

    # 配置日志格式（[backend] 前缀：容器内双写 stdout 时与 gateway 日志区分）
    log_formatter = _TzFormatter('%(asctime)s - %(levelname)s - [backend] %(filename)s:%(lineno)d - %(message)s')

    # 创建 FileHandler
    file_handler = TimedRotatingFileHandler(log_file, when='D', interval=1, backupCount=10, encoding='utf-8')
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)
    # 添加控制台输出
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    logger.addHandler(console_handler)
    logger.propagate = False  # 不传递到 root logger，避免重复输出
    return logger


logger = get_logger()
