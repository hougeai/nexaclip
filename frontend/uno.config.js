import {
  defineConfig,
  presetUno,
  presetAttributify,
  presetIcons,
  transformerDirectives
} from 'unocss'

// ============================================================
// NexaClip 设计系统 —— 浅色 · 内容优先 · 卡片化 · 柔和圆角 · 清晰 CTA
//   bg #fff / soft #f7f8fa · text #111827 · 品牌 indigo #6366f1 · 成功绿 #10b981
// 字体：Inter(正文/标题) + Noto Sans SC(中文) + JetBrains Mono(数据)
// ============================================================
export default defineConfig({
  presets: [
    presetUno(),
    presetAttributify(),
    presetIcons({
      scale: 1.2,
      extraProperties: { display: 'inline-block', 'vertical-align': 'middle' }
    })
  ],
  transformers: [transformerDirectives()],
  theme: {
    fontFamily: {
      sans: 'Inter, "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
      display: 'Inter, "Noto Sans SC", "PingFang SC", system-ui, sans-serif',
      mono: '"JetBrains Mono", ui-monospace, SFMono-Regular, monospace'
    },
    colors: {
      brand: { DEFAULT: '#6366f1', dark: '#4f46e5', soft: '#eef2ff' },
      ink: { DEFAULT: '#111827', soft: '#4b5563', faint: '#6b7280' },
      surface: { DEFAULT: '#ffffff', 1: '#f7f8fa', 2: '#eef0f4' },
      line: '#e5e7eb',
      ok: { DEFAULT: '#10b981', soft: '#d1fae5', faint: '#a7f3d0' },
      warn: { DEFAULT: '#f59e0b', soft: '#fef3c7', faint: '#fde68a' },
      bad: { DEFAULT: '#f43f5e', soft: '#ffe4e6', faint: '#fecdd3' }
    }
  },
  shortcuts: {
    // ---- 表面：白卡 + 极浅边 + 柔和投影（lilys 卡片语言） ----
    card: 'bg-white border border-line rounded-2xl shadow-[0_1px_2px_rgba(16,24,40,0.04),0_8px_24px_-12px_rgba(16,24,40,0.10)]',
    'card-hover':
      'transition-all duration-200 hover:shadow-[0_2px_4px_rgba(16,24,40,0.06),0_16px_40px_-12px_rgba(16,24,40,0.16)] hover:-translate-y-0.5',

    // ---- 按钮：胶囊/中大圆角，品牌色填充 ----
    'btn-primary':
      'inline-flex items-center justify-center gap-2 font-semibold text-white rounded-full px-6 h-11 cursor-pointer select-none ' +
      'bg-brand shadow-[0_8px_24px_-8px_rgba(99,102,241,0.5)] transition-all duration-200 ' +
      'hover:bg-brand-dark hover:shadow-[0_10px_28px_-8px_rgba(79,70,229,0.55)] active:translate-y-px ' +
      'disabled:opacity-50 disabled:pointer-events-none disabled:shadow-none ' +
      'focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/25',
    'btn-ghost':
      'inline-flex items-center justify-center gap-2 font-medium rounded-full px-5 h-11 text-ink cursor-pointer select-none ' +
      'border border-line bg-white transition-all duration-200 ' +
      'hover:bg-surface-1 hover:border-ink/20 ' +
      'focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ink/10',
    'btn-ghost-sm':
      'inline-flex items-center justify-center gap-2 font-medium rounded-lg px-3 h-8 text-xs text-ink cursor-pointer select-none ' +
      'border border-line bg-white transition-all duration-200 ' +
      'hover:bg-surface-1 hover:border-ink/20 ' +
      'focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ink/10',
    'btn-cta':
      'inline-flex items-center justify-center gap-2 font-semibold text-white rounded-full px-5 h-11 cursor-pointer select-none ' +
      'bg-ok shadow-[0_8px_24px_-8px_rgba(16,185,129,0.5)] transition-all duration-200 ' +
      'hover:brightness-105 active:translate-y-px ' +
      'focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ok/30',
    'btn-danger':
      'inline-flex items-center justify-center gap-2 font-semibold text-white rounded-full px-5 h-11 cursor-pointer select-none ' +
      'bg-bad shadow-[0_8px_24px_-8px_rgba(244,63,94,0.5)] transition-all duration-200 ' +
      'hover:brightness-105 active:translate-y-px ' +
      'focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-bad/30',

    // ---- 输入 ----
    field:
      'w-full bg-white border border-line rounded-xl px-4 text-ink placeholder-ink-faint/60 ' +
      'outline-none transition-all duration-200 ' +
      'focus:border-brand focus:ring-4 focus:ring-brand/15',

    // ---- 文本 ----
    'text-grad': 'bg-gradient-to-r from-brand to-[#a855f7] bg-clip-text text-transparent',
    'text-dim': 'text-ink-soft',

    // ---- 小件 ----
    chip: 'inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border border-line bg-surface-1 text-ink-soft',
    num: 'font-mono tabular-nums tracking-tight',
    kbd: 'num px-1.5 py-0.5 rounded bg-surface-1 border border-line text-ink-soft text-[10px]',

    // ---- 页面外壳：console 各页统一容器，宽度/边距单一事实源（新页面直接 class="page"）----
    page: 'mx-auto max-w-7xl px-4 sm:px-6 py-6',

    // ---- 侧边栏导航（ConsoleLayout 导航项统一语言）----
    'nav-item':
      'flex items-center gap-3 h-11 px-3 lg:px-3.5 rounded-xl text-sm font-medium transition-colors duration-150 justify-center lg:justify-start',
    'nav-active': 'bg-brand-soft text-brand',
    'nav-inactive': 'text-ink-soft hover:bg-surface-1 hover:text-ink',

    // ---- Tab 切换（VideoDetailModal 等多 tab 场景的统一语言）----
    'tab-item': 'px-3.5 h-8 rounded-lg text-sm cursor-pointer transition-colors',
    'tab-active': 'bg-brand-soft text-brand font-600',
    'tab-inactive': 'text-ink-faint hover:text-ink',

    // ---- 分段控制（语种 / 音色选择器等，有边框的 inactive 态）----
    'seg-item':
      'px-2.5 h-7 rounded-lg text-xs cursor-pointer transition-colors disabled:opacity-40 disabled:cursor-not-allowed',
    'seg-active': 'bg-brand-soft text-brand font-600',
    'seg-inactive': 'border border-line text-ink-faint hover:text-ink',

    // ---- 过渡时长语义化（消除 duration-150/200/280/300/500 散写）----
    'trans-base': 'transition-all duration-200 ease-out',
    'trans-fast': 'transition-all duration-150 ease-out',
    'trans-slow': 'transition-all duration-300 ease-out',

    // ---- 品牌渐变（ProgressBar / text-grad 共用填充色）----
    'grad-brand': 'bg-gradient-to-r from-brand to-[#a855f7]',

    // ---- 层级语义（消除 z-[90]/z-[99]/z-[100] 魔法数字）----
    'z-pop': 'z-[50]',
    'z-modal': 'z-[90]',
    'z-toast': 'z-[100]',

    // ---- 视频封面遮罩（MediaCard / VideoDetailModal 封面徽章共用）----
    'overlay-dark': 'bg-black/70 text-white px-1.5 py-0.5 rounded-md text-xs',
    'overlay-dark-sm': 'bg-black/60 text-white w-7 h-7 rounded-full grid place-items-center',
    'overlay-light':
      'bg-white/90 text-ink-soft text-xs px-2 py-0.5 rounded-full font-medium shadow-sm',

    // ---- Markdown 渐进增强（LLM 报告 / 问答答案共用）----
    'prose-report': 'text-sm leading-relaxed text-ink-soft'
  },
  rules: [[/^font-(\d+)$/, ([, d]) => ({ 'font-weight': d })]],
  animations: [
    ['fade-in', { enter: 'fade-in .5s ease both' }],
    ['rise', { enter: 'rise .55s cubic-bezier(.22,1,.36,1) both' }]
  ]
})
