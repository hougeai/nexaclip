"""URL 提取与安全校验（SSRF 防护）。

用户常整段粘贴 App 分享文案（口令 + 标题 + #话题 + 短链 + 引导语），故先用正则抠出
http(s) 链接，再做静态校验。

任意 URL 直喂下载引擎的风险：它能拉取任意 http(s) 资源，被构造成探测内网
（如 http://169.254.169.254/ 拿云元数据、http://127.0.0.1:6379/ 打 Redis）。
校验内容：拒私网/回环/链路本地 + 拒纯 IP 与非常规端口（scheme 由提取正则限定为 http/https）。

注意：这是**第一道**防线。DNS 解析后的 IP 校验（防 DNS rebinding）需要自定义 resolver，
yt-dlp 内部自己解析，无法在调用前拦截；因此生产环境还应配合容器网络隔离/出口代理白名单。
"""

import ipaddress
import re
from urllib.parse import urlparse

# 从任意文本里抠出第一个 http(s) 链接：字符集只含 URL 合法 ASCII，遇中文/空格自然截断；
# scheme 硬限定 http/https，file/magnet 等一律不匹配。
_URL_RE = re.compile(r"""https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+""")

# 常见站点端口；出现其它端口（尤其内网服务端口）视为可疑
ALLOWED_PORTS = {None, 80, 443, 8080, 8443}

# 私有/保留网段（IPv4 + IPv6）
_BLOCKED_NETS = [
    ipaddress.ip_network(net)
    for net in (
        '0.0.0.0/8',
        '10.0.0.0/8',
        '100.64.0.0/10',  # CGNAT
        '127.0.0.0/8',  # 回环
        '169.254.0.0/16',  # 链路本地（云元数据）
        '172.16.0.0/12',
        '192.0.0.0/24',
        '192.168.0.0/16',
        '198.18.0.0/15',
        '224.0.0.0/4',  # 组播
        '::1/128',
        'fc00::/7',  # 唯一本地
        'fe80::/10',  # 链路本地
    )
]

# 主机名是否为 IP 字面量
_IPV4_RE = re.compile(r'^\d{1,3}(\.\d{1,3}){3}$')


class UrlError(ValueError):
    """URL 校验不通过。继承 ValueError，令 api 层的 `except ValueError` 统一转 Fail。"""


def _is_blocked_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # 是域名，交给网络层隔离兜底
    return any(ip in net for net in _BLOCKED_NETS)


def validate_media_url(url: str) -> str:
    """从输入（可能是整段分享文案）提取 http(s) 链接并做 SSRF 校验，返回干净 URL。"""
    m = _URL_RE.search(url or '')
    if not m:
        raise UrlError('未识别到有效链接，请粘贴 http/https 网址')
    url = m.group()
    if len(url) > 2048:
        raise UrlError('链接过长')

    parsed = urlparse(url)  # scheme 已由 _URL_RE 保证为 http/https
    host = parsed.hostname
    if not host:
        raise UrlError('链接缺少主机名')

    # 拒 IP 字面量（域名也可能解析到内网，但 IP 直连几乎一定是探测）
    if _IPV4_RE.match(host) or ':' in host:
        if _is_blocked_ip(host):
            raise UrlError('不允许访问内网地址')
        # 纯公网 IP 也拒：正常媒体站点都是域名，IP 直连是典型滥用特征
        raise UrlError('请使用站点域名而非 IP 直连')

    if parsed.port not in ALLOWED_PORTS:
        raise UrlError('不允许的端口')

    return url


def validate_media_urls(urls: list[str]) -> list[str]:
    """批量校验，任一不通过即抛错（探测入口在 gather 前统一挡）。"""
    return [validate_media_url(u) for u in urls]
