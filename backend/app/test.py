"""NexaClip 后端端到端冒烟脚本。

流程：探活 → 登录 → 提交 URL（探测即自动下载）→ 轮询到终态 → 打印下载链接 → 历史/统计。

用法：
    cd backend && uv run python app/test.py [视频URL]
    # 不带参数用内置默认 URL；带参数则测你给的链接
"""

import os
import sys
import time

import httpx

HOST = 'http://127.0.0.1:3003'  # 与 .env BACK_END_PORT 一致
API = '/api/v1'
client = httpx.Client(base_url=HOST, timeout=120)

# 默认管理员（.env SUPER_ADMIN_* 首启自动建）
ADMIN_EMAIL = os.getenv('SUPER_ADMIN_EMAIL', 'admin@example.com')
ADMIN_PASSWORD = os.getenv('SUPER_ADMIN_PASSWORD', 'change-me')

# 默认测试链接：YouTube 第一个视频《Me at the zoo》，19 秒、体积小，适合冒烟
youtube_url = 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
# 备用：抖音短链，测国内渠道分流（f2）
douyin_url = 'https://v.douyin.com/RtzM_kSdH4Q/'
douyin_user_url = (
    'https://www.douyin.com/user/MS4wLjABAAAAci1ZqGsBao14JJkBt44mfjQVUOWmPl_gXebWpMCOvX7Rlq6H2ROcQlSwTq-zGAVb'
)
url = sys.argv[1] if len(sys.argv) > 1 else youtube_url


def _data(resp: httpx.Response) -> dict:
    """统一拆 {code,msg,data}；非 200 直接退出，让测试失败可见。"""
    resp.raise_for_status()
    body = resp.json()
    if body.get('code') != 200:
        raise SystemExit(f'✗ 接口失败 [{resp.request.method} {resp.request.url.path}]: {body.get("msg")}')
    return body


def smoke_test() -> None:
    """端到端冒烟：探活 → 登录 → 采集 → 轮询 → 取文件链接 → 历史/统计。"""
    print(f'目标链接：{url}\n')

    # 探活
    b = _data(client.get(f'{API}/system/health'))
    print(f'✓ health: {b["data"]}')

    # 登录
    b = _data(client.post(f'{API}/base/access_token', json={'email': ADMIN_EMAIL, 'password': ADMIN_PASSWORD}))
    headers = {'token': b['data']['access_token']}
    print(f'✓ 登录成功，access_token 前 12 位：{headers["token"][:12]}…')

    # 提交链接：探测即自动下载
    b = _data(client.post(f'{API}/media/probe', headers=headers, json={'urls': [url]}))
    item = b['data'][0]
    if not item.get('ok'):
        raise SystemExit(f'✗ 采集失败：{item.get("error")}')
    vid = item['vid']
    print(f'✓ 采集：《{item["title"]}》 时长 {item["duration"]}s 站点 {item["extractor"]} 状态 {item["status"]}')

    # 轮询到终态（ready/failed）
    deadline = time.time() + 600
    last_line = ''
    while True:
        if time.time() > deadline:
            raise SystemExit(f'✗ 轮询超时（600s）：采集 {vid} 未到终态')
        t = _data(client.get(f'{API}/media/videos', headers=headers, params={'ids': vid}))['data'][0]
        line = f'  · {t["status"]} percent={t.get("percent")} speed={t.get("speed")} eta={t.get("eta")}'
        if line != last_line:
            print(line)
            last_line = line
        if t['status'] in {'ready', 'failed'}:
            break
        time.sleep(1.5)

    # 结果
    if t['status'] != 'ready':
        raise SystemExit(f'✗ 下载未成功：status={t["status"]} error={t.get("error")}')
    link = t.get('download_url') or ''
    if link.startswith('/'):
        link = HOST + link
    size_mb = (t.get('size') or 0) / 1024 / 1024
    print(f'✓ 下载就绪：{t.get("filename")}（{size_mb:.1f}MB）\n  下载链接：{link}')

    # 历史/统计
    b = _data(client.get(f'{API}/media/history', headers=headers, params={'page': 1, 'page_size': 5}))
    print(f'✓ 历史记录：共 {b.get("total")} 条，本页 {len(b["data"])} 条')
    d = _data(client.get(f'{API}/media/stats', headers=headers))['data']
    print(f'✓ 用量统计：累计 {d["total_videos"]} 条 / 今日 {d["today_videos"]} / 总字节 {d["total_bytes"]}')


def kb_smoke() -> None:
    """知识库冒烟：登录 → 回填索引 → 等完成 → 搜片段 → 跨视频问答（断言引用）。

    用法：uv run python app/test.py kb（前提：库里已有转写完成的视频）
    """
    b = _data(client.post(f'{API}/base/access_token', json={'email': ADMIN_EMAIL, 'password': ADMIN_PASSWORD}))
    headers = {'token': b['data']['access_token']}
    print('✓ 登录成功\n')

    queued = _data(client.post(f'{API}/media/kb/reindex', headers=headers))['data']['queued']
    print(f'✓ 补索引：{queued} 个视频排队')
    s = {}
    for _ in range(200):
        s = _data(client.get(f'{API}/media/kb/status', headers=headers))['data']
        print(f'  · 已索引 {s["indexed"]}/{s["total"]} running={s["running"]} failed={s["failed"]}')
        if not s['running']:
            break
        time.sleep(1.5)
    if not s.get('indexed'):
        raise SystemExit('✗ 库里没有已索引视频，无法测检索（先跑 smoke_test 采一条带语音的视频）')

    chunks = _data(client.post(f'{API}/media/kb/search', headers=headers, json={'q': '核心观点', 'top_k': 5}))['data']
    if not chunks:
        raise SystemExit('✗ 检索无结果')
    for c in chunks[:3]:
        print(f'✓ 检索命中：《{c["title"]}》 {c["start"]:.0f}s  {c["text"][:40]}…')

    b = _data(client.post(f'{API}/media/kb/ask', headers=headers, json={'question': '这个视频讲了什么？依据哪几段？'}))[
        'data'
    ]
    print(f'✓ 问答（改写为「{b.get("rewritten")}」）：\n{b["answer"][:300]}\n…')
    if not b['citations']:
        raise SystemExit('✗ 问答无引用')
    for c in b['citations'][:3]:
        print(f'  · [{c["n"]}] 《{c["title"]}》 {c["start"]:.0f}s')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'kb':
        kb_smoke()
    else:
        smoke_test()
