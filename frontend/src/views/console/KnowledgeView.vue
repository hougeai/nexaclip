<script setup>
import api from '@/api'
import { marked } from 'marked'
import { formatDuration } from '@/utils'

const ui = useUiStore()

const EMPTY = '__empty__' // 下拉里「未归类」的取值（后端约定：空串=未归类，缺省=不限）

const mode = ref('search') // search | ask
const q = ref('')
const groups = ref([])
const scopePlatform = ref('') // '' = 全部平台
const scopeUploader = ref('') // '' = 全部账号

// ---- 索引状态 ----
const kb = ref(null)
let statusTimer = null
let mounted = true

const pendingCount = computed(() => {
  if (!kb.value) return 0
  return Math.max(0, kb.value.total - kb.value.indexed - kb.value.running - kb.value.failed)
})

async function loadStatus() {
  try {
    kb.value = (await api.kbStatus()).data
  } catch {
    /* 忽略 */
  }
}

async function doReindex() {
  try {
    const body = await api.kbReindex()
    const n = body.data?.queued ?? 0
    ui.info(n ? `已排队 ${n} 个视频补索引` : '无需补索引')
    if (n) startStatusPolling()
    await loadStatus()
  } catch (e) {
    ui.error(e.message || '操作失败')
  }
}

function startStatusPolling() {
  stopStatusPolling()
  statusTimer = setInterval(async () => {
    if (!mounted) return
    await loadStatus()
    if (kb.value && !kb.value.running) stopStatusPolling()
  }, 2000)
}
function stopStatusPolling() {
  if (statusTimer) {
    clearInterval(statusTimer)
    statusTimer = null
  }
}

// ---- 检索范围下拉 ----
const platformOptions = computed(() => [
  { value: '', label: '全部平台' },
  ...groups.value.map(p => ({ value: p.name || EMPTY, label: p.name || '未归类' }))
])

const uploaderOptions = computed(() => {
  const counts = {}
  const src =
    scopePlatform.value === ''
      ? groups.value
      : groups.value.filter(p => (p.name || EMPTY) === scopePlatform.value)
  for (const p of src) for (const u of p.uploaders) counts[u.name] = (counts[u.name] || 0) + u.n
  return [
    { value: '', label: '全部账号' },
    ...Object.entries(counts).map(([name, n]) => ({
      value: name || EMPTY,
      label: `${name || '未归类'}（${n}）`
    }))
  ]
})

watch(scopePlatform, () => {
  scopeUploader.value = ''
})

function scopePayload() {
  const s = {}
  if (scopePlatform.value) s.platform = scopePlatform.value === EMPTY ? '' : scopePlatform.value
  if (scopeUploader.value) s.uploader = scopeUploader.value === EMPTY ? '' : scopeUploader.value
  return Object.keys(s).length ? s : null
}

// ---- 搜片段 ----
const searching = ref(false)
const results = ref(null) // null = 尚未搜索

async function doSearch() {
  const query = q.value.trim()
  if (!query) return ui.info('请输入检索关键词')
  searching.value = true
  try {
    results.value = (await api.kbSearch({ q: query, scope: scopePayload(), top_k: 10 })).data
    q.value = '' // 搜索后清空输入框
  } catch (e) {
    ui.error(e.message || '检索失败')
  } finally {
    searching.value = false
  }
}

// ---- 问知识库 ----
const messages = ref([]) // [{q, a, rewritten, citations}]
const asking = ref(false)
const chatEl = ref(null)

async function doAsk() {
  const question = q.value.trim()
  if (!question || asking.value) return
  asking.value = true
  const t0 = Date.now() // 提问发出时刻；回答到达时刻与差值用于查看模型延时
  try {
    const history = messages.value.map(m => ({ q: m.q, a: m.answer })).slice(-6)
    const body = await api.kbAsk({ question, history, scope: scopePayload() })
    messages.value.push({ q: question, ts: t0, ats: Date.now(), ms: Date.now() - t0, ...body.data })
    q.value = '' // 提问后清空输入框
  } catch (e) {
    ui.error(e.message || '问答失败')
  } finally {
    asking.value = false
  }
}

function clearChat() {
  messages.value = []
}

watch(
  () => [messages.value.length, asking.value],
  () => nextTick(() => chatEl.value && (chatEl.value.scrollTop = chatEl.value.scrollHeight))
)

const renderMd = text => (text ? marked.parse(text.replace(/</g, '&lt;'), { async: false }) : '')

// ---- 引用/片段 → 详情弹窗并跳进度 ----
const selected = ref(null)
const seekTo = ref(0)

async function openVideo(vid, start = 0) {
  seekTo.value = start || 0
  try {
    selected.value = (await api.getVideo(vid)).data
  } catch (e) {
    ui.error(e.message || '加载视频失败')
    selected.value = null
  }
}

async function modalRemove(r) {
  try {
    await api.removeVideo(r.vid)
    selected.value = null
    ui.success('已删除')
    loadStatus()
  } catch (e) {
    ui.error(e.message)
  }
}

async function modalRetranscribe(r) {
  try {
    await api.retranscribe(r.vid)
    if (selected.value?.vid === r.vid)
      selected.value = { ...selected.value, transcript_error: null }
    ui.info('已重新提交转写，稍后重开弹窗查看结果')
    loadStatus()
  } catch (e) {
    ui.error(e.message || '操作失败')
  }
}

function onMeta({ vid, title, uploader }) {
  if (selected.value?.vid === vid)
    selected.value = {
      ...selected.value,
      ...(title ? { title } : {}),
      ...(uploader ? { uploader } : {})
    }
}

async function modalRetry(r) {
  if (!r.url) return ui.error('缺少原始链接')
  try {
    const body = await api.probe({ urls: [r.url] })
    const item = body.data[0]
    if (!item.ok) return ui.error(item.error || '重新提交失败')
    ui.info('已重新提交，进度请到采集记录查看')
    selected.value = null
  } catch (e) {
    ui.error(e.message || '重新提交失败')
  }
}

// 秒 → mm:ss
const fmtTs = s => {
  const t = Math.floor(s || 0)
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`
}

// 毫秒时间戳 → HH:MM:SS（对话气泡时间戳，看模型回复延时用）
const fmtClock = ms => {
  const d = new Date(ms)
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map(n => String(n).padStart(2, '0'))
    .join(':')
}

onMounted(async () => {
  try {
    groups.value = (await api.historyGroups()).data
  } catch {
    /* 忽略 */
  }
  await loadStatus()
  if (kb.value?.running) startStatusPolling()
})
onUnmounted(() => {
  stopStatusPolling()
  mounted = false
})
</script>

<template>
  <div class="page">
    <header class="mb-5 flex items-end justify-between gap-4 flex-wrap">
      <div>
        <h1 class="font-display text-2xl font-800 tracking-tight">知识库</h1>
        <p class="text-dim text-sm mt-1">跨全部采集视频语义检索与问答，引用可直跳视频进度</p>
      </div>
      <!-- 索引状态胶囊 + 补索引 -->
      <div v-if="kb" class="flex items-center gap-2 pb-0.5">
        <div
          class="flex items-center gap-2 h-9 px-3 rounded-xl bg-white border border-line shadow-sm"
        >
          <span class="i-lucide-database text-brand text-sm" />
          <span class="num text-sm font-700 text-ink">{{ kb.indexed }}/{{ kb.total }}</span>
          <span class="text-xs text-ink-faint">已索引</span>
          <span v-if="kb.running" class="i-lucide-loader-circle animate-spin text-xs text-brand" />
          <span v-else-if="kb.failed" class="text-xs text-bad">{{ kb.failed }} 失败</span>
        </div>
        <button
          v-if="!kb.running && (pendingCount > 0 || kb.failed > 0)"
          class="btn-ghost !h-9 !px-3 !text-sm"
          title="为未索引/索引失败的视频补建向量索引"
          @click="doReindex"
        >
          <span class="i-lucide-refresh-ccw" />补索引
        </button>
      </div>
    </header>

    <!-- 空库提示 -->
    <div v-if="kb && !kb.total" class="card flex items-center gap-3 p-4 mb-5 text-sm text-ink-soft">
      <span class="i-lucide-inbox text-xl text-ink-faint" />
      还没有转写完成的视频：去「视频采集」页贴链接或上传文件，入库后会自动转写并建索引。
    </div>

    <!-- 模式切换 + 检索范围 -->
    <div class="flex items-center gap-3 flex-wrap mb-4">
      <div class="flex gap-1 p-1 bg-surface-2 rounded-xl">
        <button
          class="px-4 h-8 rounded-lg text-sm cursor-pointer transition-colors"
          :class="
            mode === 'search'
              ? 'bg-white shadow-sm font-600 text-ink'
              : 'text-ink-faint hover:text-ink'
          "
          @click="mode = 'search'"
        >
          <span class="i-lucide-search mr-1" />搜片段
        </button>
        <button
          class="px-4 h-8 rounded-lg text-sm cursor-pointer transition-colors"
          :class="
            mode === 'ask'
              ? 'bg-white shadow-sm font-600 text-ink'
              : 'text-ink-faint hover:text-ink'
          "
          @click="mode = 'ask'"
        >
          <span class="i-lucide-bot mr-1" />问知识库
        </button>
      </div>
      <div class="ml-auto flex gap-2">
        <select
          v-model="scopePlatform"
          class="h-9 px-3 rounded-lg bg-white border border-line text-sm text-ink-soft outline-none cursor-pointer"
        >
          <option v-for="o in platformOptions" :key="o.value" :value="o.value">
            {{ o.label }}
          </option>
        </select>
        <select
          v-model="scopeUploader"
          class="h-9 px-3 rounded-lg bg-white border border-line text-sm text-ink-soft outline-none cursor-pointer max-w-[12rem]"
        >
          <option v-for="o in uploaderOptions" :key="o.value" :value="o.value">
            {{ o.label }}
          </option>
        </select>
      </div>
    </div>

    <!-- 输入行 -->
    <div class="flex gap-2 mb-5">
      <input
        v-model="q"
        type="text"
        class="flex-1 h-11 px-4 rounded-xl bg-white border border-line text-sm outline-none focus:border-brand transition-colors"
        :placeholder="mode === 'search' ? '检索视频片段，回车搜索' : '向整个视频库提问，回车作答'"
        @keyup.enter="mode === 'search' ? doSearch() : doAsk()"
      />
      <button
        v-if="mode === 'ask' && messages.length"
        class="w-11 h-11 shrink-0 grid place-items-center rounded-xl border border-line text-ink-faint hover:text-brand hover:border-brand cursor-pointer transition-colors"
        title="清空对话，重新开始"
        @click="clearChat"
      >
        <span class="i-lucide-message-square-plus text-lg" />
      </button>
      <button
        class="btn-cta !h-11 shrink-0"
        :disabled="searching || asking"
        @click="mode === 'search' ? doSearch() : doAsk()"
      >
        <span
          :class="
            searching || asking
              ? 'i-lucide-loader-circle animate-spin'
              : mode === 'search'
                ? 'i-lucide-search'
                : 'i-lucide-send'
          "
        />{{ mode === 'search' ? '搜索' : '提问' }}
      </button>
    </div>

    <!-- ===== 搜片段结果 ===== -->
    <template v-if="mode === 'search'">
      <div
        v-if="results === null"
        class="card flex flex-col items-center justify-center py-20 text-ink-faint gap-3"
      >
        <span class="i-lucide-file-search text-4xl opacity-40" />
        <span class="text-sm">输入关键词，跨全部视频的转写片段做混合检索（语义 + 关键词）</span>
      </div>
      <div
        v-else-if="!results.length"
        class="card flex flex-col items-center justify-center py-20 text-ink-faint gap-3"
      >
        <span class="i-lucide-search-x text-4xl opacity-40" />
        <span class="text-sm">没有检索到相关片段</span>
      </div>
      <div v-else class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        <article
          v-for="(c, i) in results"
          :key="i"
          class="card card-hover overflow-hidden flex flex-col cursor-pointer group"
          @click="openVideo(c.vid, c.start)"
        >
          <!-- 封面 -->
          <div class="relative aspect-video bg-surface-2 overflow-hidden">
            <img
              v-if="c.thumbnail"
              :src="c.thumbnail"
              :alt="c.title || ''"
              referrerpolicy="no-referrer"
              class="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
              loading="lazy"
            />
            <div v-else class="w-full h-full grid place-items-center text-ink-faint/30">
              <span class="i-lucide-video text-4xl" />
            </div>
            <span
              v-if="c.duration"
              class="num absolute bottom-2 right-2 px-1.5 py-0.5 rounded-md bg-black/70 text-xs text-white"
            >
              {{ formatDuration(c.duration) }}
            </span>
            <span
              v-if="c.extractor"
              class="absolute top-2 left-2 text-xs px-2 py-0.5 rounded-full bg-white/90 text-ink-soft font-medium shadow-sm"
            >
              {{ c.extractor }}
            </span>
            <!-- hover：跳转播放 -->
            <span
              class="absolute bottom-2 left-2 flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 transition-opacity"
            >
              <span class="i-lucide-play text-sm" />{{ fmtTs(c.start) }}-{{ fmtTs(c.end) }}
            </span>
          </div>

          <!-- 正文 -->
          <div class="p-3.5 flex flex-col gap-2.5 flex-1">
            <div>
              <h4 class="text-sm font-medium leading-snug line-clamp-2 text-ink min-h-[2.4em]">
                {{ c.title || c.vid }}
              </h4>
              <p v-if="c.uploader" class="text-xs text-ink-faint mt-1 truncate">
                {{ c.uploader }}
              </p>
            </div>

            <!-- 片段内容（命中片段，核心信息） -->
            <p class="text-sm text-ink-soft leading-relaxed line-clamp-3">{{ c.text }}</p>

            <!-- 底部：时间区间 + 相关性分 -->
            <div class="mt-auto flex items-center justify-between text-xs text-ink-faint pt-1">
              <span class="num text-brand tabular-nums flex items-center gap-0.5">
                <span class="i-lucide-play text-[10px]" />{{ fmtTs(c.start) }}-{{ fmtTs(c.end) }}
              </span>
              <span
                v-if="c.score != null"
                class="num tabular-nums text-ink-faint/60"
                :title="`相关性 ${c.score}`"
              >
                {{ c.score.toFixed(3) }}
              </span>
            </div>
          </div>
        </article>
      </div>
    </template>

    <!-- ===== 问知识库对话 ===== -->
    <div
      v-else
      ref="chatEl"
      class="card p-4 sm:p-5 space-y-4 min-h-[24rem] max-h-[calc(100vh-21rem)] overflow-y-auto"
    >
      <div
        v-if="!messages.length"
        class="min-h-[18rem] flex flex-col items-center justify-center text-ink-faint text-sm text-center px-6 gap-2"
      >
        <span class="i-lucide-bot text-3xl opacity-40" />
        <span>跨全部采集视频提问，例如「我收藏的课程里是怎么讲索引优化的」</span>
        <span class="text-xs">答案附可点击的时间戳引用，点击直达原视频进度</span>
      </div>
      <template v-for="(m, i) in messages" :key="i">
        <div class="flex flex-col items-end gap-1">
          <span class="num text-[10px] text-ink-faint/60 tabular-nums px-1">{{
            fmtClock(m.ts)
          }}</span>
          <div
            class="max-w-[52rem] rounded-2xl rounded-br-md bg-brand text-white px-4 py-2.5 text-sm leading-relaxed break-words"
          >
            {{ m.q }}
          </div>
        </div>
        <div>
          <div class="text-[10px] text-ink-faint/60 mb-1 flex items-center gap-1.5 px-1">
            <span class="num tabular-nums">{{ fmtClock(m.ats) }}</span>
            <span v-if="m.ms != null" class="num">· 用时 {{ (m.ms / 1000).toFixed(1) }}s</span>
          </div>
          <div
            v-if="m.rewritten && m.rewritten !== m.q"
            class="text-xs text-ink-faint mb-1 flex items-center gap-1"
          >
            <span class="i-lucide-wand-sparkles" />按「{{ m.rewritten }}」检索
          </div>
          <div class="max-w-[52rem] rounded-2xl rounded-bl-md bg-surface-2 px-4 py-3">
            <div
              class="prose-answer text-sm leading-relaxed text-ink-soft"
              v-html="renderMd(m.answer)"
            />
            <div
              v-if="m.citations?.length"
              class="flex flex-wrap gap-1.5 mt-3 pt-3 border-t border-line"
            >
              <button
                v-for="c in m.citations"
                :key="c.n"
                class="chip !py-0.5 cursor-pointer hover:!border-brand hover:!text-brand transition-colors"
                :title="c.snippet"
                @click="openVideo(c.vid, c.start)"
              >
                <span class="num text-brand font-600">[{{ c.n }}]</span>
                <span class="max-w-[10rem] truncate">{{ c.title || c.vid }}</span>
                <span class="num tabular-nums">{{ fmtTs(c.start) }}</span>
              </button>
            </div>
          </div>
        </div>
      </template>
      <div v-if="asking" class="flex items-center gap-2 text-xs text-ink-faint">
        <span class="i-lucide-loader-circle animate-spin" />正在检索片段并生成回答…
      </div>
    </div>

    <!-- 详情弹窗（引用/片段点击，带进度跳转） -->
    <VideoDetailModal
      :video="selected"
      :initial-seek="seekTo"
      @close="selected = null"
      @retry="modalRetry"
      @remove="modalRemove"
      @retranscribe="modalRetranscribe"
      @meta="onMeta"
    />
  </div>
</template>

<style scoped>
/* 答案 Markdown 渲染（与报告弹窗同款精简样式） */
.prose-answer :deep(h1),
.prose-answer :deep(h2),
.prose-answer :deep(h3) {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--c-ink, #111827);
  margin: 0.6rem 0 0.35rem;
}
.prose-answer :deep(h1:first-child),
.prose-answer :deep(h2:first-child),
.prose-answer :deep(p:first-child) {
  margin-top: 0;
}
.prose-answer :deep(p) {
  margin: 0.35rem 0;
}
.prose-answer :deep(ul),
.prose-answer :deep(ol) {
  padding-left: 1.25rem;
  margin: 0.35rem 0;
}
.prose-answer :deep(li) {
  margin: 0.15rem 0;
  list-style: disc;
}
.prose-answer :deep(strong) {
  color: var(--c-ink, #111827);
}
.prose-answer :deep(code) {
  background: rgba(17, 24, 39, 0.06);
  border-radius: 4px;
  padding: 0.1rem 0.35rem;
  font-size: 0.8em;
}
.prose-answer :deep(blockquote) {
  border-left: 3px solid var(--c-line, #e5e7eb);
  padding-left: 0.75rem;
  color: var(--c-ink-faint, #6b7280);
  margin: 0.4rem 0;
}
</style>
