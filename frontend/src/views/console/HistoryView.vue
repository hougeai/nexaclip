<script setup>
import api from '@/api'
import { formatDuration, formatBytes, statusMeta, fromNow } from '@/utils'
import ConfirmDialog from '@/components/ConfirmDialog.vue'

const ui = useUiStore()

const ALL = '全部'
const PAGE_SIZE = 24

const groups = ref([]) // [{name, n, uploaders: [{name, n}]}]
const records = ref([])
const stats = ref(null)
const subAvatars = ref({}) // uploader→avatar，来自订阅表
const loading = ref(false) // 首屏分组加载
const recLoading = ref(false)
const recPage = ref(1)
const recTotal = ref(0)
const activePlatform = ref(ALL)
const activeUploader = ref(null) // 原始账号名（'' = 未归类）；null = 未选
const selected = ref(null)
let pollTimer = null
let mounted = true

// ---- 排序：分页在服务端，排序必须由后端做（key 对齐 controllers/video.py 白名单）----
const SORTS = [
  { key: 'pub_desc', label: '最新发布在前' },
  { key: 'pub_asc', label: '最早发布在前' },
  { key: 'coll_desc', label: '最新采集在前' },
  { key: 'coll_asc', label: '最早采集在前' }
]
const sort = ref('pub_desc')
const sortOpen = ref(false)
const sortLabel = computed(() => SORTS.find(s => s.key === sort.value)?.label ?? '')

function selectSort(key) {
  sortOpen.value = false
  if (sort.value === key) return
  sort.value = key
  loadRecords(true)
}

const disp = n => n || '未归类'

// UTC 秒 → 本地短日期
const fmtDay = ts => (ts ? new Date(ts * 1000).toLocaleDateString('zh-CN') : '')

// ---- 分组导航（来自后端 groups，轻量） ----
const platformTabs = computed(() => {
  const total = groups.value.reduce((s, p) => s + p.n, 0)
  return [{ name: ALL, n: total }, ...groups.value]
})

const uploaderList = computed(() => {
  if (activePlatform.value === ALL) {
    const counts = {}
    for (const p of groups.value)
      for (const u of p.uploaders) counts[u.name] = (counts[u.name] || 0) + u.n
    return Object.entries(counts)
      .map(([name, n]) => ({ name, n }))
      .sort((a, b) => b.n - a.n)
  }
  return groups.value.find(p => p.name === activePlatform.value)?.uploaders || []
})

// ---- 记录分页（只拉当前选中格子） ----
async function loadRecords(reset = false) {
  if (recLoading.value) return
  if (activeUploader.value === null) {
    records.value = []
    recTotal.value = 0
    return
  }
  recLoading.value = true
  try {
    if (reset) {
      recPage.value = 1
      records.value = []
    }
    const params = { page: recPage.value, page_size: PAGE_SIZE, sort: sort.value }
    if (activePlatform.value !== ALL) params.platform = activePlatform.value
    params.uploader = activeUploader.value // 空串=未归类组，始终带上
    const body = await api.history(params)
    records.value = reset ? body.data : [...records.value, ...body.data]
    recTotal.value = body.total
  } catch (e) {
    ui.error(e.message || '加载失败')
  } finally {
    recLoading.value = false
  }
}

async function loadGroups() {
  try {
    groups.value = (await api.historyGroups()).data
  } catch (e) {
    ui.error(e.message || '加载失败')
  }
}

function selectPlatform(name) {
  if (activePlatform.value === name) return
  activePlatform.value = name
  activeUploader.value = uploaderList.value[0]?.name ?? null
  loadRecords(true)
}

function selectUploader(name) {
  if (activeUploader.value === name) return
  activeUploader.value = name
  loadRecords(true)
}

function loadMore() {
  if (recLoading.value) return
  recPage.value += 1
  loadRecords()
}

async function loadStats() {
  try {
    stats.value = (await api.stats()).data
  } catch {
    /* 忽略 */
  }
}

async function loadSubAvatars() {
  try {
    const body = (await api.subList()).data
    subAvatars.value = body.avatars || {}
  } catch {
    /* 忽略 */
  }
}

// ---- 轮询：仅当前展示记录里进行中的 ----
// ready 且文案未出结果（无正文也无报错）= 转写中，也要轮——否则「重新转写」后弹窗/卡片不刷新
const activeVids = () =>
  records.value
    .filter(
      r =>
        r.vid &&
        (statusMeta(r.status).active ||
          r.ai_status === 'running' ||
          (r.status === 'ready' && !r.transcript_len && !r.transcript_error))
    )
    .map(r => r.vid)

function ensurePolling() {
  if (!pollTimer) pollTimer = setInterval(pollOnce, 5000)
}
function maybeStopPolling() {
  if (pollTimer && !activeVids().length) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}
async function pollOnce() {
  const ids = activeVids()
  if (!ids.length) return maybeStopPolling()
  if (!mounted) return
  try {
    const body = await api.pollVideos(ids)
    const map = Object.fromEntries(body.data.map(v => [v.vid, v]))
    // stats 只在下载状态「发生转变」时刷；合并前算，否则旧状态被覆盖比不出差异
    const changed = body.data.some(v => {
      const old = records.value.find(r => r.vid === v.vid)
      return old && old.status !== v.status && ['ready', 'failed'].includes(v.status)
    })
    records.value = records.value.map(r => (r.vid && map[r.vid] ? { ...r, ...map[r.vid] } : r))
    if (selected.value && map[selected.value.vid]) {
      selected.value = { ...selected.value, ...map[selected.value.vid] }
      const v = map[selected.value.vid]
      // lite 轮询不含正文/报告/高光明细，状态转 done 时补拉一次全量
      if (
        (v.transcript_len && !selected.value.transcript) ||
        (v.ai_status === 'done' && !selected.value.ai_report)
      )
        openDetail(selected.value)
    }
    if (changed) loadStats()
  } catch {
    /* 单次失败忽略 */
  }
  maybeStopPolling()
}
onUnmounted(() => {
  pollTimer && clearInterval(pollTimer)
  mounted = false
})

// ---- 打开详情：列表是 lite 数据，先即时展示卡片信息，再拉全量（文案/章节/报告）合并 ----
async function openDetail(r) {
  selected.value = r
  try {
    const body = await api.getVideo(r.vid)
    if (selected.value && selected.value.vid === r.vid) {
      selected.value = { ...selected.value, ...body.data }
    }
  } catch {
    /* 拉全量失败则停留在卡片信息 */
  }
}

// ---- 弹窗内操作：重试 / 重新转写 / 删除 ----
async function retranscribe(r) {
  try {
    await api.retranscribe(r.vid)
    const patch = { transcript_error: null }
    const i = records.value.findIndex(x => x.vid === r.vid)
    if (i >= 0) records.value[i] = { ...records.value[i], ...patch }
    if (selected.value?.vid === r.vid) selected.value = { ...selected.value, ...patch }
    ui.info('已重新提交转写')
    ensurePolling()
  } catch (e) {
    ui.error(e.message || '操作失败')
  }
}

async function reanalyze(r) {
  try {
    await api.reanalyze(r.vid)
    const patch = { ai_status: 'running', ai_error: null }
    const i = records.value.findIndex(x => x.vid === r.vid)
    if (i >= 0) records.value[i] = { ...records.value[i], ...patch }
    if (selected.value?.vid === r.vid) selected.value = { ...selected.value, ...patch }
    ui.info('已重新提交 AI 分析')
    ensurePolling()
  } catch (e) {
    ui.error(e.message || '操作失败')
  }
}

// 编辑标题/账号后：弹窗即时反映 + 重拉分组和记录（uploader 变了记录会换账号组）
function onMeta({ vid, title, uploader }) {
  if (selected.value?.vid === vid)
    selected.value = {
      ...selected.value,
      ...(title ? { title } : {}),
      ...(uploader ? { uploader } : {})
    }
  loadGroups()
  loadRecords(true)
}

async function retry(r) {
  if (!r.url) return ui.error('缺少原始链接')
  try {
    const body = await api.probe({ urls: [r.url] })
    const item = body.data[0]
    if (!item.ok) return ui.error(item.error || '重新提交失败')
    const i = records.value.findIndex(x => x.vid === item.vid)
    if (i >= 0) records.value[i] = { ...records.value[i], ...item }
    if (selected.value && selected.value.vid === item.vid) {
      selected.value = { ...selected.value, ...item }
    }
    ui.info('已重新提交，开始下载')
    ensurePolling()
  } catch (e) {
    ui.error(e.message || '重新提交失败')
  }
}

const confirmState = ref({ open: false, title: '', message: '', danger: false, resolve: null })

function confirmDialog(opts) {
  return new Promise(resolve => {
    confirmState.value = { open: true, resolve, ...opts }
  })
}

function onConfirmResolve(val) {
  confirmState.value.resolve?.(val)
  confirmState.value.open = false
}

async function remove(r) {
  const ok = await confirmDialog({
    title: '删除采集记录',
    message: `确定删除？\n成品文件与转写/报告/索引一并清除，不可恢复。`,
    confirmText: '删除',
    cancelText: '取消',
    danger: true
  })
  if (!ok) return
  try {
    await api.removeVideo(r.vid)
    records.value = records.value.filter(x => x.vid !== r.vid)
    recTotal.value = Math.max(0, recTotal.value - 1)
    if (selected.value?.vid === r.vid) selected.value = null
    ui.success('已删除')
    loadStats()
    await loadGroups() // 导航计数变化；当前格子空了则自动切到下一个账号
    if (!uploaderList.value.some(u => u.name === activeUploader.value)) {
      activeUploader.value = uploaderList.value[0]?.name ?? null
      loadRecords(true)
    }
  } catch (e) {
    ui.error(e.message)
  }
}

onMounted(async () => {
  loading.value = true
  await loadGroups()
  loadSubAvatars()
  loading.value = false
  activeUploader.value = uploaderList.value[0]?.name ?? null
  loadRecords(true)
  loadStats()
  ensurePolling()
})
</script>

<template>
  <div class="page">
    <header class="mb-6 flex items-end justify-between gap-4 flex-wrap">
      <div>
        <h1 class="font-display text-2xl font-800 tracking-tight">采集记录</h1>
        <p class="text-dim text-sm mt-1">按平台与账号分组，点击卡片查看详情</p>
      </div>
      <!-- 用量：单条分段统计条，比三个散胶囊更整体 -->
      <div
        v-if="stats"
        class="flex items-center h-11 rounded-xl bg-white border border-line shadow-sm animate-fade-in"
      >
        <div class="flex items-center gap-2 px-4 h-full">
          <span class="i-lucide-archive text-brand text-sm" />
          <span class="num text-sm font-700 text-ink">{{ stats.total_videos }}</span>
          <span class="text-xs text-ink-faint">累计</span>
        </div>
        <span class="w-px h-4 bg-line" />
        <div class="flex items-center gap-2 px-4 h-full">
          <span class="i-lucide-calendar-days text-ok text-sm" />
          <span class="num text-sm font-700 text-ink">{{ stats.today_videos }}</span>
          <span class="text-xs text-ink-faint">今日</span>
        </div>
        <span class="w-px h-4 bg-line" />
        <div class="flex items-center gap-2 px-4 h-full">
          <span class="i-lucide-hard-drive text-ink-faint text-sm" />
          <span class="num text-sm font-700 text-ink">{{ formatBytes(stats.total_bytes) }}</span>
          <span class="text-xs text-ink-faint">存储</span>
        </div>
      </div>
    </header>

    <!-- 首屏加载：布局骨架（侧栏+卡片网格），避免整页干转 -->
    <div v-if="loading" class="flex flex-col sm:flex-row gap-5" aria-hidden="true">
      <div class="w-full sm:w-52 shrink-0 card p-2">
        <div v-for="i in 6" :key="i" class="h-10 rounded-xl bg-surface-2 animate-pulse mb-1" />
      </div>
      <div class="flex-1 min-w-0 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        <div v-for="i in 6" :key="i" class="card overflow-hidden">
          <div class="aspect-video bg-surface-2 animate-pulse" />
          <div class="p-3.5 space-y-2.5">
            <div class="h-4 w-4/5 rounded bg-surface-2 animate-pulse" />
            <div class="h-3 w-2/5 rounded bg-surface-2 animate-pulse" />
            <div class="h-3 w-full rounded bg-surface-2 animate-pulse" />
          </div>
        </div>
      </div>
    </div>
    <!-- 全局空态：给出明确的下一步动作 -->
    <div
      v-else-if="!groups.length"
      class="card flex flex-col items-center justify-center py-24 text-ink-faint gap-4 animate-fade-in"
    >
      <div class="w-16 h-16 rounded-2xl bg-surface-1 grid place-items-center">
        <span class="i-lucide-clapperboard text-3xl opacity-40" />
      </div>
      <div class="text-center">
        <p class="text-ink-soft font-600">还没有采集记录</p>
        <p class="text-xs mt-1.5 max-w-[22rem] leading-relaxed">
          到采集页粘贴链接或上传文件，视频会自动进入 下载 → 转写 → AI 报告 → 知识库索引 流水线
        </p>
      </div>
      <RouterLink :to="{ name: 'collect' }" class="btn-primary !h-10 !px-6 text-sm no-underline">
        <span class="i-lucide-plus" />去采集视频
      </RouterLink>
    </div>

    <template v-else>
      <!-- 平台筛选：胶囊分段按钮，active 品牌实底 + 柔光投影 -->
      <div class="flex gap-2 flex-wrap mb-5">
        <button
          v-for="p in platformTabs"
          :key="p.name"
          class="inline-flex items-center gap-1.5 h-8 px-3.5 rounded-full text-sm cursor-pointer transition-all duration-200"
          :class="
            activePlatform === p.name
              ? 'bg-brand text-white font-600 shadow-[0_4px_12px_-4px_rgba(99,102,241,0.5)]'
              : 'bg-white border border-line text-ink-soft hover:border-brand/40 hover:text-brand'
          "
          @click="selectPlatform(p.name)"
        >
          {{ p.name === ALL ? ALL : disp(p.name) }}
          <span
            class="num text-[10px] leading-4 px-1.5 rounded-full"
            :class="
              activePlatform === p.name ? 'bg-white/20 text-white' : 'bg-surface-2 text-ink-faint'
            "
          >
            {{ p.n }}
          </span>
        </button>
      </div>

      <!-- 主体：账号导航（卡片化，桌面端 sticky）+ 卡片网格（主要内容） -->
      <div class="flex flex-col sm:flex-row gap-5">
        <aside class="w-full sm:w-52 shrink-0 sm:sticky sm:top-6 self-start">
          <div class="card p-2">
            <div class="text-xs text-ink-faint font-600 px-2.5 pt-1 pb-2 hidden sm:block">
              账号 · <span class="num">{{ uploaderList.length }}</span>
            </div>
            <div
              class="flex sm:flex-col gap-1 overflow-x-auto sm:overflow-x-hidden pb-0.5 sm:pb-0 sm:max-h-[calc(100vh-16rem)] sm:overflow-y-auto"
            >
              <button
                v-for="u in uploaderList"
                :key="u.name || '__none__'"
                class="shrink-0 sm:shrink w-max sm:w-full flex items-center gap-2 pl-1.5 pr-2 sm:pr-2.5 h-10 rounded-xl text-sm cursor-pointer transition-colors duration-200 text-left"
                :class="
                  activeUploader === u.name
                    ? 'bg-brand-soft text-brand font-600'
                    : 'text-ink-soft hover:bg-surface-1 hover:text-ink'
                "
                @click="selectUploader(u.name)"
              >
                <img
                  v-if="subAvatars[u.name]"
                  :src="subAvatars[u.name]"
                  :alt="u.name"
                  referrerpolicy="no-referrer"
                  class="w-6 h-6 shrink-0 rounded-full object-cover"
                  @error="$event.target.style.display = 'none'"
                />
                <span
                  v-else
                  class="w-6 h-6 shrink-0 rounded-full grid place-items-center text-[11px] font-700"
                  :class="
                    activeUploader === u.name
                      ? 'bg-brand text-white'
                      : 'bg-surface-2 text-ink-faint'
                  "
                >
                  {{ (u.name || 'U').slice(0, 1) }}
                </span>
                <span class="truncate max-w-[8.5rem]">{{ disp(u.name) }}</span>
                <span
                  class="num text-[10px] px-1.5 py-0.5 rounded-full ml-auto hidden sm:inline-block"
                  :class="
                    activeUploader === u.name
                      ? 'bg-white text-brand'
                      : 'bg-surface-1 text-ink-faint'
                  "
                >
                  {{ u.n }}
                </span>
              </button>
            </div>
          </div>
        </aside>

        <div class="flex-1 min-w-0">
          <!-- 结果工具条：条数 + 排序说明 -->
          <div
            v-if="records.length"
            class="flex items-center justify-between mb-3 text-xs text-ink-faint"
          >
            <span>
              「{{ disp(activeUploader) }}」共
              <span class="num text-ink-soft font-600">{{ recTotal }}</span> 条
            </span>
            <!-- 排序下拉：惯例样式「图标 + 当前项 + ▾」，菜单单选带勾；透明遮罩点外即关 -->
            <div class="relative">
              <button
                class="flex items-center gap-1.5 h-7 px-2.5 rounded-lg border border-line bg-white text-ink-soft text-xs cursor-pointer hover:border-ink/25 hover:text-ink transition-colors"
                @click="sortOpen = !sortOpen"
              >
                <span class="i-lucide-arrow-up-down" />
                {{ sortLabel }}
                <span
                  class="i-lucide-chevron-down text-[10px] transition-transform duration-200"
                  :class="sortOpen && 'rotate-180'"
                />
              </button>
              <div v-if="sortOpen" class="fixed inset-0 z-20" @click="sortOpen = false" />
              <div
                v-if="sortOpen"
                class="card absolute right-0 top-full mt-1.5 z-30 w-36 p-1 animate-fade-in"
              >
                <button
                  v-for="s in SORTS"
                  :key="s.key"
                  class="w-full flex items-center justify-between gap-2 px-2.5 h-8 rounded-lg text-xs cursor-pointer transition-colors text-left"
                  :class="
                    sort === s.key
                      ? 'bg-brand-soft text-brand font-600'
                      : 'text-ink-soft hover:bg-surface-1 hover:text-ink'
                  "
                  @click="selectSort(s.key)"
                >
                  {{ s.label }}
                  <span v-if="sort === s.key" class="i-lucide-check text-[10px]" />
                </button>
              </div>
            </div>
          </div>

          <!-- 加载：骨架卡片；空态：图标 + 引导 CTA -->
          <div
            v-if="recLoading && !records.length"
            class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4"
            aria-hidden="true"
          >
            <div v-for="i in 6" :key="i" class="card overflow-hidden">
              <div class="aspect-video bg-surface-2 animate-pulse" />
              <div class="p-3.5 space-y-2.5">
                <div class="h-4 w-4/5 rounded bg-surface-2 animate-pulse" />
                <div class="h-3 w-2/5 rounded bg-surface-2 animate-pulse" />
                <div class="h-3 w-full rounded bg-surface-2 animate-pulse" />
              </div>
            </div>
          </div>
          <div
            v-else-if="!records.length"
            class="card flex flex-col items-center justify-center py-20 text-ink-faint gap-3"
          >
            <span class="i-lucide-folder-open text-4xl opacity-40" />
            <span class="text-sm">「{{ disp(activeUploader) }}」下暂无记录</span>
            <RouterLink :to="{ name: 'collect' }" class="text-xs text-brand hover:underline">
              去采集一条 →
            </RouterLink>
          </div>
          <template v-else>
            <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
              <article
                v-for="r in records"
                :key="r.vid"
                class="card card-hover overflow-hidden flex flex-col cursor-pointer group animate-rise"
                @click="openDetail(r)"
              >
                <!-- 封面 -->
                <div class="relative aspect-video bg-surface-2 overflow-hidden">
                  <img
                    v-if="r.thumbnail"
                    :src="r.thumbnail"
                    :alt="r.title || ''"
                    referrerpolicy="no-referrer"
                    class="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
                    loading="lazy"
                  />
                  <div v-else class="w-full h-full grid place-items-center text-ink-faint/30">
                    <span class="i-lucide-video text-4xl" />
                  </div>
                  <!-- 底部渐变压暗：时长/详情角标在任何封面上都可读 -->
                  <div
                    class="absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-black/45 to-transparent pointer-events-none"
                  />
                  <span
                    v-if="r.duration"
                    class="num absolute bottom-2 right-2 px-1.5 py-0.5 rounded-md bg-black/70 text-xs text-white"
                  >
                    {{ formatDuration(r.duration) }}
                  </span>
                  <span
                    v-if="r.extractor"
                    class="absolute top-2 left-2 text-xs px-2 py-0.5 rounded-full bg-white/90 text-ink-soft font-medium shadow-sm"
                  >
                    {{ r.extractor }}
                  </span>
                  <span
                    v-if="r.status !== 'ready'"
                    class="absolute top-2 right-2 text-xs px-2 py-0.5 rounded-full font-medium"
                    :class="statusMeta(r.status).cls"
                  >
                    {{ statusMeta(r.status).label }}
                  </span>
                  <!-- hover：查看详情 -->
                  <span
                    class="absolute bottom-2 left-2 flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    <span class="i-lucide-maximize-2 text-sm" />详情
                  </span>
                </div>

                <!-- 正文 -->
                <div class="p-3.5 flex flex-col gap-2.5 flex-1">
                  <div>
                    <h4
                      class="text-sm font-medium leading-snug line-clamp-2 text-ink min-h-[2.4em] transition-colors duration-200 group-hover:text-brand"
                    >
                      {{ r.title || r.url }}
                    </h4>
                    <p v-if="r.uploader" class="text-xs text-ink-faint mt-1 truncate">
                      {{ r.uploader }}
                    </p>
                  </div>

                  <!-- 状态节点 -->
                  <StatusStepper
                    :status="r.status"
                    :has-transcript="!!r.transcript_len"
                    :transcript-failed="!!r.transcript_error"
                    :ai-status="r.ai_status"
                  />

                  <!-- 下载中：进度条 -->
                  <div v-if="statusMeta(r.status).active" class="space-y-1.5">
                    <div class="flex items-center justify-between text-xs">
                      <span class="text-brand font-medium">下载中</span>
                      <span class="num text-ink-faint">
                        <template v-if="r.percent != null">{{ r.percent.toFixed(0) }}%</template>
                        <template v-if="r.speed"> · {{ r.speed }}</template>
                      </span>
                    </div>
                    <ProgressBar :percent="r.percent" />
                  </div>
                  <!-- 失败：错误摘要 -->
                  <p v-else-if="r.status === 'failed'" class="text-xs text-bad line-clamp-1">
                    {{ r.error || '下载失败' }}
                  </p>
                </div>

                <!-- 页脚：通栏浅灰条，元信息与正文分层，扫读更快 -->
                <div
                  class="mt-auto px-3.5 py-2.5 border-t border-line/70 bg-surface-1/60 flex items-center justify-between text-xs text-ink-faint"
                >
                  <span class="flex items-center gap-2">
                    <span v-if="r.transcript_len" class="flex items-center gap-0.5">
                      <span class="i-lucide-file-text" />{{ r.transcript_len }}字
                    </span>
                    <span
                      v-if="r.ai_chapters_count"
                      class="text-brand flex items-center gap-0.5 font-600"
                    >
                      <span class="i-lucide-sparkles" />{{ r.ai_chapters_count }}章
                    </span>
                  </span>
                  <span class="flex items-center gap-1.5">
                    <span v-if="r.size" class="num">{{ formatBytes(r.size) }}</span>
                    <span v-if="r.size" class="text-line">·</span>
                    <span>{{ fmtDay(r.publish_at) || fromNow(r.created_at) }}</span>
                  </span>
                </div>
              </article>
            </div>

            <div class="mt-6 text-center">
              <button
                v-if="records.length < recTotal"
                class="btn-ghost !h-10 text-sm"
                :disabled="recLoading"
                @click="loadMore"
              >
                <span
                  :class="
                    recLoading ? 'i-lucide-loader-circle animate-spin' : 'i-lucide-chevron-down'
                  "
                />加载更多（{{ records.length }}/{{ recTotal }}）
              </button>
              <p v-else class="text-xs text-ink-faint/60 flex items-center justify-center gap-3">
                <span class="w-10 h-px bg-line" />已经到底啦<span class="w-10 h-px bg-line" />
              </p>
            </div>
          </template>
        </div>
      </div>
    </template>

    <!-- 详情弹窗 -->
    <VideoDetailModal
      :video="selected"
      @close="selected = null"
      @retry="retry"
      @remove="remove"
      @retranscribe="retranscribe"
      @reanalyze="reanalyze"
      @meta="onMeta"
    />

    <ConfirmDialog
      v-model:open="confirmState.open"
      :title="confirmState.title"
      :message="confirmState.message"
      :confirm-text="confirmState.confirmText"
      :cancel-text="confirmState.cancelText"
      :danger="confirmState.danger"
      @confirm="onConfirmResolve(true)"
      @cancel="onConfirmResolve(false)"
    />
  </div>
</template>
