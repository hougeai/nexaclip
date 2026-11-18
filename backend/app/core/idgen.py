"""对外标识生成。

- gen_id：对外标识（user_id 8 位 / vid 12 位），ALNUM 无符号
  ——vid 会进成品文件名与下载 URL，符号易被转义；- 开头会被命令行当 flag
"""

from nanoid import generate

# 标识字母表：大小写字母 + 数字，不含符号
_ALNUM = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'


def gen_id(size: int = 12) -> str:
    """对外标识（ALNUM 无符号）。默认 12 位约 3.2×10^21 种取值；
    user_id 特例用 8 位约 2.2×10^14 种，靠 unique 索引 + save 碰撞重试兜底。"""
    return generate(_ALNUM, size=size)
