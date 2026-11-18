# 抖音 SecSDK 签名维护手册

抖音 Web 端的 `/aweme/v1/web/aweme/detail/` 位于字节 Argus 风控网关之后，非浏览器请求必须携带 **`x-secsdk-web-signature`** 签名，否则一律 403。本项目 2026-11 实测复现该管控；社区记录其收紧时间在 2026-09 前后（二手信息，未逐条核实）。

本文记录签名算法、常量位置，以及**抖音换盐后如何更新**。

---

## 1. 错误链：先判断卡在哪一层

响应体是纯文本，直接看内容即可定位：

| 响应体 | 含义 | 处理 |
|--------|------|------|
| `403 Blocked by ArgusSecurityPlugin Uifid Not Found` | 请求没带 `uifid`（或 cookies 里没有有效 `UIFID`） | 更新 cookies / 检查 `UIFID` |
| `403 Blocked by ArgusSecurityPlugin Signature Not Found` | `uifid` 有了，但签名缺失或算错 | 检查签名逻辑、时间戳、盐值 |
| `403 Blocked by ArgusSecurityPlugin Validate Error` | `uifid` 存在但服务端校验不过（伪造/过期） | 重新导出 cookies |
| `200` 但 `aweme_detail` 为空 | 不是鉴权问题 | 另查（作品权限/图集/删稿等） |

> 代码里的 `APIResponseError` 用 `%r` 打印时只会显示消息，**状态码在 `e.status_code` 属性里**；`str(e)` 才带 ` Status Code: 403`。日志里现在已显式打印 `status=`。

---

## 2. 代码位置

| 内容 | 位置 |
|------|------|
| 盐值常量 | `backend/app/core/douyin.py` → `_WEBSIGN_SALT` |
| 签名函数 | `backend/app/core/douyin.py` → `_sign_secsdk()` |
| 取 uifid | `backend/app/core/douyin.py` → `_uifid_from_cookie()` |
| 接管 detail 请求 | `_SecSdkDouyinCrawler.fetch_post_detail()` + 替换 `f2.apps.douyin.handler.DouyinCrawler` |

---

## 3. 签名算法

```text
规范化 query：
  原 query 按 & 切分 → 每对按第一个 = 切开 → 键/值先 decode
  → 键和值都用 quote(safe='*-._') 重新编码 → 按原顺序用 & 连接

追加参数：
  canon & "uifid=" & quote(uifid) & "timestamp=" & ts

签名：
  sig = md5(f"{uifid}_{ts}_{SALT}_{signed_query}").hexdigest()   # 32 位小写

最终 URL：
  {base}?{signed_query}&x-secsdk-web-signature={sig}
```

要点：

- **`ts` 是秒级**（10 位），不是毫秒；同时进 MD5 明文和 query 的 `timestamp=`。
- **只签 URL 就够了**（实测：签名只放请求头依然 403，必须进 query）。
- 规范化必须与**实际发出的 URL 字节**一致，否则等于对平台没见过的串签名。
- `uifid` 取 cookies 里的 `UIFID`；为空则不加签名（会 403）。

---

## 4. 换盐步骤（方法 A：从抖音自己的 JS 里找）

**触发信号**：cookies 里 `UIFID` 有效，但仍是 `Signature Not Found`，且签名代码逻辑没动过 → 优先怀疑盐值变了；其次怀疑 query 规范化规则变了（用方法 C 与浏览器实际签名比对即可区分）。

1. Chrome 打开 `https://www.douyin.com/`，F12 → **Network**
2. 过滤框输入 `bytegoofy` / `runtime_bundler` / `secsdk`
3. 找到 **`runtime_bundler_34.js`**
   - `34` 是 `douyin_web` 的 project-id，**只有这个 bundle 的盐适用于网页端**
4. 把 JS 保存下来，找 32 位大写十六进制常量：

   ```bash
   grep -oE '"([0-9A-F]{32})"' runtime_bundler_34.js | sort -u
   ```

5. 里面可能有多个候选。该常量位于 SecSDK 字节码 VM 的**字符串表第 39 号**（`secsdk-strategy` 的 `project-id=34`），结合签名相关逻辑上下文确认。
6. 改 `core/douyin.py` 的 `_WEBSIGN_SALT`，重启后端，按下节验证。

### 方法 B：看上游仓库是否已更新（最省事）

换盐时先查这几个项目的常量是否已更新，比自查 JS 快：

- `Evil0ctal/Douyin_TikTok_Download_API` → `src/dtk/signing/native/websign.py`（`SALT`）
- `1545468557/douyin2text` → `douyin2text/douyin.py`（`WEBSIGN_SALT`）
- `xinxiuzhu/media-parser` → `docs/parsers/douyin.md`


## 5. 注意事项

- **盐按 project 区分**：只有 `project-id=34`（`douyin_web`）适用；拿 TikTok/其他字节系 bundle 的盐一定不对。
- **`UIFID` 必须来自有效 cookies**：`-` 过期后会从 `Signature Not Found` 变回 `Uifid Not Found` 或 `Validate Error`。
- **f2 升级风险**：补丁依赖 `f2.apps.douyin.handler` 模块内的 `DouyinCrawler` 名字。升级 f2 后若该引用改名/移除，补丁会静默失效（回到 403），需同步核对。
- Microsoft/搜索引擎 UA 之类的"绕过"已失效，不要在这上面浪费时间。
- 仅供个人合法用途，遵守平台条款与著作权法。