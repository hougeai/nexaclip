# NexaClip 前端技术栈总结

## 一、技术栈总览

| 层级 | 技术 | 版本 |
|------|------|------|
| 构建工具 | Vite | ^7.0.6 |
| 前端框架 | Vue 3（Composition API / `<script setup>`） | ^3.5.18 |
| 路由 | Vue Router | ^4.5.1 |
| 状态管理 | Pinia + pinia-plugin-persistedstate | ^3.0.3 / ^4.5.0 |
| HTTP 客户端 | Axios（自定义拦截器 + token 刷新） | ^1.12.2 |
| 样式方案 | UnoCSS（preset-uno / attributify / icons / directives） | ^66.5.1 |
| 图标 | @iconify-json（lucide / ri / simple-icons） | — |
| 自动导入 | unplugin-auto-import + unplugin-vue-components | ^20.1.0 / ^29.0.0 |
| 工具库 | @vueuse/core、dayjs、marked | ^13.9.0 / ^1.11.18 / ^18.0.11 |
| 代码风格 | ESLint 9（flat config）+ eslint-plugin-vue + Prettier | ^9.31.0 / ~10.3.0 / 3.6.2 |
| 语言 | JavaScript（无 TypeScript） | — |
| 包管理 | pnpm | — |
| Node | ^20.19.0 \|\| >=22.12.0 | — |

## 二、项目结构

```
frontend/
├── public/
├── src/
│   ├── api/          # http.js（axios 封装）+ index.js（全量接口表）
│   ├── components/   # ConfirmDialog / MediaCard / ProgressBar / StatusStepper / Toaster / UrlComposer / VideoDetailModal
│   ├── config/       # API_BASE 配置
│   ├── layouts/      # ConsoleLayout（侧边栏控制台布局）
│   ├── router/       # index.js（路由 + 路由守卫，内联 auth guard）
│   ├── stores/       # auth.js（登录态）+ ui.js（toast 通知）
│   ├── styles/       # index.css（控件重置 + 动画关键帧 + 无障碍）
│   ├── utils/        # token 管理 / 格式化 / URL 解析 / 状态元信息
│   ├── views/        # LandingView / AuthView / ResetPasswordView / console/（Download / History / Knowledge / Subscriptions）
│   ├── App.vue       # 全局入口（RouterView + Transition + Toaster）
│   └── main.js       # Vue + Pinia + Router 初始化
├── eslint.config.js
├── index.html
├── jsconfig.json
├── package.json
├── pnpm-lock.yaml
├── uno.config.js     # 设计系统（颜色 / 字体 / 快捷类 / 动画）
└── vite.config.js    # 代理 / 插件 / alias
```

## 三、关键设计决策

### 3.1 设计系统（uno.config.js）
- **主题**：浅色 · 内容优先 · 卡片化 · 柔和圆角
- **色彩**：品牌 indigo `#6366f1`，背景 `#fff` / `#f7f8fa`，文字 `#111827`，成功绿 `#10b981`
- **字体**：Inter（正文/标题）+ Noto Sans SC（中文）+ JetBrains Mono（数据）
- **快捷类**：card / btn-primary / btn-ghost / btn-cta / btn-danger / field / chip / num / page

### 3.2 认证体系
- JWT token 存 localStorage（`nexaclip_access` / `nexaclip_refresh`）
- Axios 请求拦截器自动注入 `token` header
- 响应拦截器：401/419 → 单飞刷新 → 重试一次；刷新失败 → 清 token → ApiError
- Pinia `auth` store 持久化 user 信息，`authed` 标志驱动 UI

### 3.3 路由
- 公开页：`/`（落地页）、`/login`、`/register`、`/reset-password`
- 控制台：`/app`（requiresAuth）下 4 个子页 — collect / history / kb / subs
- 路由守卫：未登录重定向到 login（带 next 参数），已登录禁止访问 guestOnly 页
- 404 重定向到 `/`

### 3.4 构建配置
- Vite 代理：`/api` → `127.0.0.1:VITE_BACKEND_PORT`（默认 3003）
- 生产环境通过 Nginx 反代，业务代码 baseURL 一致（无环境分支）
- 代码分割：chunkSizeWarningLimit=1500
