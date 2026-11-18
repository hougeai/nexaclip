"""转写文本语种自动检测（零依赖，按文字脚本特征）。

定位：给 Video.lang_source 赋值，供前端展示「源语言」并从目标语言选项中排除。
翻译本身不强依赖它（LLM 能自行识别原文），检测不出来存 None 即可，不阻塞链路。

判定序：假名 → 谚文 → 汉字 → 拉丁兜底。假名必须排最前——日文混大量汉字，
出现任一假名即为日语；拉丁字母统一判 en（四种支持语种里唯一的拉丁语言）。
"""

from typing import Optional

_SAMPLE = 3000  # 只取前段样本，全片文本越长没必要


def detect_lang(text: str) -> Optional[str]:
    """转写全文 → 语种代码（zh/en/ja/ko，与 translate.LANGS 对齐）；判不出返回 None。"""
    if not text:
        return None
    sample = text[:_SAMPLE]
    total = len(sample)

    if any('\u3040' <= c <= '\u30ff' for c in sample):  # 假名（先于汉字！）
        return 'ja'
    if _ratio(sample, '\uac00', '\ud7af') > 0.1:  # 谚文
        return 'ko'
    if _ratio(sample, '\u4e00', '\u9fff') > 0.15:  # 汉字
        return 'zh'
    if sum(1 for c in sample if c.isascii() and c.isalpha()) / total > 0.4:  # 拉丁为主
        return 'en'
    return None


def _ratio(s: str, lo: str, hi: str) -> float:
    return sum(1 for c in s if lo <= c <= hi) / len(s)
