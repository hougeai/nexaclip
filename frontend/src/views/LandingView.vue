<script setup>
const router = useRouter()
const auth = useAuthStore()

const features = [
  {
    icon: 'i-lucide-link-2',
    title: '全渠道采集',
    desc: '覆盖抖音、B 站、YouTube 等上千站点，批量解析、自动下载，亦支持本地文件导入。'
  },
  {
    icon: 'i-lucide-rss',
    title: '订阅自动入库',
    desc: '关注创作者频道，定时检测新增内容，采集、转写、索引全流程无人值守。'
  },
  {
    icon: 'i-lucide-file-text',
    title: '句级转写',
    desc: '逐句生成带时间戳文本，点击任意语句即刻定位至对应视频进度。'
  },
  {
    icon: 'i-lucide-sparkles',
    title: 'AI 图文报告',
    desc: '自动生成大纲、章节时间轴与图文摘要，一小时视频两分钟通览。'
  },
  {
    icon: 'i-lucide-bot',
    title: '语义检索问答',
    desc: '跨库向量与关键词混合召回，答案附时间戳引用，一键回溯原视频。'
  },
  {
    icon: 'i-lucide-clapperboard',
    title: '高光与多语言配音',
    desc: '智能标注时间轴要点并导出片段；支持多语种字幕翻译与 AI 配音。'
  }
]

const steps = [
  { n: '1', title: '注册登录', desc: '邮箱注册，即刻开始。' },
  { n: '2', title: '添加来源', desc: '粘贴链接、上传文件或订阅频道。' },
  { n: '3', title: '自动处理', desc: '下载、转写、报告与索引全程托管。' },
  { n: '4', title: '检索使用', desc: '问答、检索、切片导出、翻译配音。' }
]

const sites = [
  '抖音',
  'Bilibili',
  'YouTube',
  'TikTok',
  'Twitter / X',
  'Vimeo',
  'Reddit',
  'SoundCloud'
]

function start() {
  router.push(auth.isLoggedIn ? { name: 'collect' } : { name: 'register' })
}
</script>

<template>
  <div class="min-h-full bg-white text-ink">
    <!-- ===== 顶部导航 ===== -->
    <header class="sticky top-0 z-40 bg-white/80 backdrop-blur-md border-b border-line">
      <nav class="mx-auto max-w-6xl px-5 h-16 flex items-center justify-between">
        <RouterLink to="/" class="flex items-center gap-2.5">
          <img src="/favicon.svg" alt="NexaClip" class="w-8 h-8" />
          <span class="font-display text-lg font-800 tracking-tight"
            >Nexa<span class="text-grad">Clip</span></span
          >
        </RouterLink>
        <div class="flex items-center gap-2 sm:gap-4">
          <a
            href="#features"
            class="hidden sm:inline text-sm text-dim hover:text-ink transition-colors"
            >功能</a
          >
          <a href="#how" class="hidden sm:inline text-sm text-dim hover:text-ink transition-colors"
            >如何使用</a
          >
          <template v-if="auth.isLoggedIn">
            <RouterLink :to="{ name: 'collect' }" class="btn-primary !h-9 !px-4 !text-sm"
              >进入控制台</RouterLink
            >
          </template>
          <template v-else>
            <RouterLink
              :to="{ name: 'login' }"
              class="text-sm text-dim hover:text-ink transition-colors"
              >登录</RouterLink
            >
            <RouterLink :to="{ name: 'register' }" class="btn-primary !h-9 !px-4 !text-sm"
              >免费开始</RouterLink
            >
          </template>
        </div>
      </nav>
    </header>

    <!-- ===== Hero ===== -->
    <section class="relative overflow-hidden">
      <div
        class="absolute inset-x-0 top-0 h-[420px] bg-gradient-to-b from-brand-soft/70 to-white pointer-events-none"
      />
      <div class="relative mx-auto max-w-6xl px-5 pt-20 pb-16 text-center">
        <span class="chip mx-auto mb-6 !bg-white !border-brand/30 !text-brand">
          <span class="i-lucide-sparkles" />AI 视频知识库 · 转写 · 报告 · 语义问答
        </span>
        <h1 class="font-display font-800 tracking-tight leading-[1.1] text-[2.75rem] sm:text-6xl">
          让每个视频，都成为<span class="text-grad">可检索的知识</span>。
        </h1>
        <p class="mt-5 text-lg sm:text-xl text-dim max-w-2xl mx-auto leading-relaxed">
          自动下载、句级转写、AI 报告与向量索引，
          <br class="hidden sm:block" />为个人视频资产提供跨库检索、语义问答与多语言配音。
        </p>
        <div class="mt-9 flex items-center justify-center gap-3">
          <button class="btn-primary !h-12 !px-8 !text-base" @click="start">
            免费开始<span class="i-lucide-arrow-right" />
          </button>
          <RouterLink
            v-if="!auth.isLoggedIn"
            :to="{ name: 'login' }"
            class="btn-ghost !h-12 !px-7 !text-base"
          >
            已有账号，登录
          </RouterLink>
        </div>

        <!-- 产品演示 mock -->
        <div class="mt-14 mx-auto max-w-3xl card p-4 sm:p-5 text-left animate-rise">
          <div class="flex items-center gap-2 mb-3">
            <span class="w-3 h-3 rounded-full bg-bad/70" />
            <span class="w-3 h-3 rounded-full bg-warn/70" />
            <span class="w-3 h-3 rounded-full bg-ok/70" />
          </div>
          <div class="flex items-center gap-2 bg-surface-1 border border-line rounded-xl px-4 h-12">
            <span class="i-lucide-link text-ink-faint" />
            <span class="text-ink-faint text-sm">https://www.youtube.com/watch?v=…</span>
            <span class="ml-auto btn-primary !h-8 !px-4 !text-xs">获取</span>
          </div>
          <div class="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div class="rounded-xl border border-line overflow-hidden">
              <div
                class="aspect-video bg-gradient-to-br from-brand/15 to-[#a855f7]/15 grid place-items-center"
              >
                <span class="i-lucide-play-circle text-3xl text-brand/60" />
              </div>
              <div class="p-3">
                <div class="h-3 w-3/4 rounded bg-surface-2" />
                <div class="mt-2 h-2.5 w-1/2 rounded bg-surface-2" />
                <div class="mt-3 flex items-center gap-1.5">
                  <span class="chip !text-[10px] !py-0.5 !bg-brand !text-white !border-brand"
                    >视频下载中</span
                  >
                  <span class="num text-[10px] text-ink-faint">62%</span>
                </div>
              </div>
            </div>
            <div class="rounded-xl border border-line overflow-hidden p-3">
              <div class="flex items-center gap-1.5">
                <span class="i-lucide-bot text-sm text-brand" />
                <span class="text-xs text-ink-soft">这条视频讲了什么工具？</span>
              </div>
              <div class="mt-2 rounded-lg bg-surface-1 p-2.5">
                <div class="h-2.5 w-5/6 rounded bg-surface-2" />
                <div class="mt-1.5 h-2.5 w-2/3 rounded bg-surface-2" />
                <div class="mt-2.5 flex flex-wrap gap-1.5 border-t border-line pt-2">
                  <span class="chip !text-[10px] !py-0.5 !border-brand/40 !text-brand"
                    >[1] 00:10</span
                  >
                  <span class="chip !text-[10px] !py-0.5 !border-brand/40 !text-brand"
                    >[2] 00:50</span
                  >
                  <span class="chip !text-[10px] !py-0.5 !border-brand/40 !text-brand"
                    >[3] 01:11</span
                  >
                </div>
              </div>
              <p class="mt-2 text-[10px] text-ink-faint">答案附时间戳引用，点击回溯原视频</p>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ===== 站点信任跑马灯（内容双份 + 位移 50% 无缝循环，mask 两端渐隐） ===== -->
    <section class="border-y border-line bg-surface-1/60 overflow-hidden">
      <div class="marquee py-6">
        <div class="marquee-track">
          <span v-for="(s, i) in [...sites, ...sites]" :key="i" class="marquee-item">
            <span class="w-1.5 h-1.5 rounded-full bg-brand/50" />
            {{ s }}
          </span>
        </div>
      </div>
    </section>

    <!-- ===== 功能 ===== -->
    <section id="features" class="mx-auto max-w-6xl px-5 py-24">
      <div class="text-center max-w-2xl mx-auto mb-14">
        <h2 class="font-display text-3xl sm:text-4xl font-800 tracking-tight">
          从采集到检索，完整闭环
        </h2>
        <p class="mt-4 text-dim text-lg">全流程自动执行，将视频内容沉淀为可复用的知识资产。</p>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
        <div v-for="f in features" :key="f.title" class="card card-hover p-6">
          <div
            class="w-11 h-11 rounded-xl bg-brand-soft grid place-items-center text-brand text-xl mb-4"
          >
            <span :class="f.icon" />
          </div>
          <h3 class="font-display font-700 text-lg">{{ f.title }}</h3>
          <p class="mt-2 text-dim leading-relaxed">{{ f.desc }}</p>
        </div>
      </div>
    </section>

    <!-- ===== 如何使用 ===== -->
    <section id="how" class="bg-surface-1/60 border-y border-line">
      <div class="mx-auto max-w-6xl px-5 py-24">
        <div class="text-center max-w-2xl mx-auto mb-14">
          <h2 class="font-display text-3xl sm:text-4xl font-800 tracking-tight">
            四步，构建个人视频知识库
          </h2>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8">
          <div v-for="s in steps" :key="s.n" class="text-center">
            <div
              class="mx-auto w-14 h-14 rounded-full bg-white border border-line grid place-items-center font-display font-800 text-brand text-xl shadow-sm"
            >
              {{ s.n }}
            </div>
            <h3 class="mt-5 font-display font-700 text-lg">{{ s.title }}</h3>
            <p class="mt-2 text-dim">{{ s.desc }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- ===== 尾部 CTA ===== -->
    <section class="mx-auto max-w-6xl px-5 py-24">
      <div
        class="relative overflow-hidden rounded-3xl bg-gradient-to-br from-brand to-[#7c3aed] px-6 py-16 text-center text-white shadow-[0_24px_60px_-20px_rgba(99,102,241,0.6)]"
      >
        <h2 class="font-display text-3xl sm:text-4xl font-800 tracking-tight">
          即刻开始，让内容沉淀为知识
        </h2>
        <p class="mt-4 text-white/80 text-lg">构建属于你的视频知识资产，随需检索与问答。</p>
        <button
          class="mt-8 inline-flex items-center gap-2 font-semibold text-brand rounded-full px-8 h-12 bg-white hover:bg-white/90 transition-colors cursor-pointer"
          @click="start"
        >
          免费开始<span class="i-lucide-arrow-right" />
        </button>
      </div>
    </section>

    <!-- ===== 页脚 ===== -->
    <footer class="border-t border-line">
      <div
        class="mx-auto max-w-6xl px-5 py-10 flex flex-col sm:flex-row items-center justify-between gap-4 text-sm text-ink-faint"
      >
        <div class="flex items-center gap-2">
          <img src="/favicon.svg" alt="NexaClip" class="w-6 h-6" />
          <span>© 2026 NexaClip</span>
        </div>
        <p>仅供个人合法用途 · 请遵守目标平台服务条款与著作权法规</p>
      </div>
    </footer>
  </div>
</template>

<style scoped>
/* 站点跑马灯：轨道内容为清单双份，位移 -50% 恰好一轮无缝衔接（padding-right 补最后项间距） */
.marquee {
  mask-image: linear-gradient(90deg, transparent, #000 12%, #000 88%, transparent);
  -webkit-mask-image: linear-gradient(90deg, transparent, #000 12%, #000 88%, transparent);
}
.marquee-track {
  display: inline-flex;
  align-items: center;
  gap: 3rem;
  padding-right: 3rem;
  white-space: nowrap;
  animation: marquee 30s linear infinite;
}
.marquee-item {
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
  font-size: 15px;
  font-weight: 600;
  color: var(--c-ink-faint, #6b7280);
}
@keyframes marquee {
  from {
    transform: translateX(0);
  }
  to {
    transform: translateX(-50%);
  }
}
/* 动效可达性：偏好减弱动效时静止 */
@media (prefers-reduced-motion: reduce) {
  .marquee-track {
    animation: none;
  }
}
</style>
