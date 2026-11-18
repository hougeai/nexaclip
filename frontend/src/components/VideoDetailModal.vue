<script setup>
import api from '@/api'
import { marked } from 'marked'
import { formatDuration, formatBytes, downloadHref, statusMeta } from '@/utils'

const props = defineProps({
  video: { type: Object, default: null }, // 采集记录；null = 关闭
  initialSeek: { type: Number, default: 0 } // >0：打开后跳到该秒（知识库引用跳转用）
})
const emit = defineEmits(['close', 'retry', 'remove', 'retranscribe', 'reanalyze', 'meta'])

const ui = useUiStore()
const videoEl = ref(null)
const tab = ref('transcript') // qa | transcript | report | highlights | subs | dub
const cur = ref(0) // 当前播放秒数（章节高亮）

// 高亮点类型 → 文案 + 配色（时间轴圆点、列表标签共用）
const HL_META = {
  quote: { label: '金句', dot: 'bg-brand', text: 'text-brand', soft: 'bg-brand-soft' },
  conflict: { label: '冲突', dot: 'bg-bad', text: 'text-bad', soft: 'bg-bad/10' },
  concept: { label: '核心概念', dot: 'bg-ok', text: 'text-ok', soft: 'bg-ok/10' },
  turn: { label: '转折', dot: 'bg-warn', text: 'text-warn', soft: 'bg-warn/10' }
}
const hlMeta = t =>
  HL_META[t] || { label: t, dot: 'bg-ink-faint', text: 'text-ink-faint', soft: 'bg-surface-2' }

// 时间轴定位基准：优先视频时长，缺失时退化为最后一个高亮点结束秒
const hlTotal = computed(() => props.video?.duration || highlights.value.at(-1)?.end || 0)
const hlPct = sec => (hlTotal.value ? Math.min(100, (sec / hlTotal.value) * 100) : 0)
// 区间宽度：极短片段保底 2%，避免细到看不见/点不到
const hlWidth = h => Math.max(2, hlPct(h.end) - hlPct(h.start))

// 导出选择：勾选的高亮点下标集合
const hlSelected = ref(new Set())
function hlToggle(i) {
  const s = new Set(hlSelected.value)
  s.has(i) ? s.delete(i) : s.add(i)
  hlSelected.value = s
}
function hlToggleAll() {
  hlSelected.value =
    hlSelected.value.size === highlights.value.length
      ? new Set()
      : new Set(highlights.value.map((_, i) => i))
}
// 高光生成状态：弹窗内部自洽（提交 → 轮询到 done 落 hlPoints），宿主无需任何配合，两个宿主零改动可用
const hlRunning = ref(false)
const hlPoints = ref([])
const hlError = ref(null)
let hlTimer = null
const hlStatus = computed(() =>
  hlRunning.value ? 'running' : hlPoints.value.length ? 'done' : props.video?.highlight_status
)
const highlights = computed(() =>
  hlPoints.value.length ? hlPoints.value : props.video?.highlights || []
)

function hlStop() {
  hlTimer && clearInterval(hlTimer)
  hlTimer = null
}
async function genHighlights() {
  if (hlRunning.value) return
  try {
    await api.genHighlights(props.video.vid)
  } catch (e) {
    return ui.error(e.message || '操作失败')
  }
  hlRunning.value = true
  hlError.value = null
  hlPoints.value = []
  hlStop()
  const vid = props.video.vid
  hlTimer = setInterval(async () => {
    if (props.video?.vid !== vid) return hlStop() // 弹窗已关/已切视频：停轮询，防定时器泄漏
    try {
      const v = (await api.getVideo(vid)).data
      if (v.highlight_status === 'done') {
        hlStop()
        hlRunning.value = false
        hlPoints.value = v.highlights || []
        ui.success('高光标注完成')
      } else if (v.highlight_status === 'failed') {
        hlStop()
        hlRunning.value = false
        hlError.value = v.highlight_error
        ui.error(v.highlight_error || '高光标注失败')
      }
    } catch {
      /* 单次轮询失败忽略，下轮再试 */
    }
  }, 5000)
}
onUnmounted(hlStop)

// 翻译 / 配音：源语言由后端转写时自动检测（video.lang_source），用户选目标语言 + 音色；
// 语种/音色表从 GET /media/translate/langs 拉（后端单一真相源）；
// srt 同步生成给下载链接；配音视频异步（弹窗内轮询 dub_status，与高光同套路）
const langs = ref({}) // { zh: { name, voices: [{id, label}] }, ... }
const langTarget = ref('en')
const dubVoice = ref('') // 音色 ID；''=该语种默认（首个）
const targetVoices = computed(() => langs.value[langTarget.value]?.voices || [])
// 未显式选择时后端用该语种默认（首个），前端高亮保持一致
const effectiveVoice = computed(() => dubVoice.value || targetVoices.value[0]?.id || '')
const dubVoiceLabel = computed(
  () =>
    langs.value[dubLangEff.value]?.voices?.find(v => v.id === dubVoiceEff.value)?.label ||
    dubVoiceEff.value ||
    ''
)
watch(langTarget, () => {
  dubVoice.value = '' // 换语种重置为默认音色
})
async function loadLangs() {
  if (Object.keys(langs.value).length) return
  try {
    langs.value = (await api.translateLangs()).data
  } catch (e) {
    ui.error(e.message || '语种列表加载失败')
  }
}
// 流程门控：配音必须先走过「生成双语字幕」（确认译文），本次或历史生成过均算
const srtReady = computed(() => !!srtUrl.value || !!props.video?.sub_exists)
function openTranslate(t) {
  tab.value = t // 'subs' | 'dub'
  loadLangs()
  if (t === 'subs' && props.video?.sub_exists && !subPairs.value.length) fetchSub() // 已有字幕直接进预览
}

// 字幕预览：拉 srt 文本前端解析（译文主行 + 原文副行），时间戳点击 seek
const subPairs = ref([])
const subLoading = ref(false)
async function fetchSub() {
  if (subLoading.value) return
  subLoading.value = true
  try {
    const text = await api.getSub(props.video.vid)
    subPairs.value = parseSrt(typeof text === 'string' ? text : '')
  } catch {
    subPairs.value = []
  } finally {
    subLoading.value = false
  }
}
function parseSrt(text) {
  const out = []
  // 注意：match 后 slice(1,5) 的数组下标从 0 起（[时,分,秒,毫秒]），别按原 match 索引取
  const ts = g => +g[0] * 3600 + +g[1] * 60 + +g[2] + +g[3] / 1000
  for (const block of text.trim().split(/\n\n+/)) {
    const lines = block.split('\n')
    const m = (lines[1] || '').match(/(\d+):(\d+):(\d+),(\d+) --> /)
    if (lines.length < 3 || !m) continue
    out.push({ start: ts(m.slice(1, 5)), target: lines[2] || '', source: lines[3] || '' })
  }
  return out
}
const translating = ref(false)
const srtUrl = ref(null)
const dubRunning = ref(false)
const dubError = ref(null)
// 轮询到的最终结果本地留存：宿主列表数据不含配音轮询，props.dub_status 可能永远停在旧值
const dubLocal = ref(null) // 'done' | 'failed' | null
const dubMeta = ref(null) // done 时 {lang, voice}
let dubTimer = null
const dubState = computed(() => {
  if (dubRunning.value) return 'running'
  if (dubLocal.value) return dubLocal.value
  return props.video?.dub_status || 'none'
})
const dubLangEff = computed(() => dubMeta.value?.lang || props.video?.dub_lang)
const dubVoiceEff = computed(() => dubMeta.value?.voice || props.video?.dub_voice)
const dubFileHref = computed(() =>
  props.video ? downloadHref(`/api/v1/media/${props.video.vid}/file?kind=dub`) : ''
)
// 播放器切源：配音完成后在左侧播放器直接切换 原片 ↔ 配音版（不跳新页面），切换续播当前进度
const showDub = ref(false)
const playerSrc = computed(() =>
  showDub.value ? dubFileHref.value : downloadHref(props.video?.download_url)
)
function toggleDubSource() {
  const at = cur.value
  showDub.value = !showDub.value
  nextTick(() => seekChapter(at))
}
function dubStop() {
  dubTimer && clearInterval(dubTimer)
  dubTimer = null
}
async function genSrt() {
  if (translating.value) return
  translating.value = true
  try {
    const body = await api.translate(props.video.vid, { lang_target: langTarget.value })
    srtUrl.value = body.data.srt_url
    ui.success(`已翻译 ${body.data.translated}/${body.data.total} 句`)
    fetchSub() // 生成完立即刷新预览
  } catch (e) {
    ui.error(e.message || '翻译失败')
  } finally {
    translating.value = false
  }
}
async function genDub() {
  if (dubRunning.value) return
  try {
    await api.dub(props.video.vid, { lang_target: langTarget.value, voice: dubVoice.value || null })
  } catch (e) {
    return ui.error(e.message || '操作失败')
  }
  dubRunning.value = true
  dubError.value = null
  dubLocal.value = null
  dubMeta.value = null
  dubStop()
  const vid = props.video.vid
  dubTimer = setInterval(async () => {
    if (props.video?.vid !== vid) return dubStop()
    try {
      const v = (await api.getVideo(vid)).data
      if (v.dub_status === 'done') {
        dubStop()
        dubRunning.value = false
        dubLocal.value = 'done'
        dubMeta.value = { lang: v.dub_lang, voice: v.dub_voice }
        ui.success('配音视频已生成')
      } else if (v.dub_status === 'failed') {
        dubStop()
        dubRunning.value = false
        dubLocal.value = 'failed'
        dubError.value = v.dub_error
        ui.error(v.dub_error || '配音生成失败')
      }
    } catch {
      /* 单次轮询失败忽略 */
    }
  }, 5000)
}
onUnmounted(dubStop)

// AI 问答（单视频）：复用 kb 管线，scope 锁本视频；索引正常由转写链自动构建，
// 仅 running 时轮询等待、failed/未索引时给手动重建按钮（历史遗留视频）
const qaMessages = ref([]) // [{q, a, rewritten, citations}]（不持久化，关弹窗即清）
const qaInput = ref('')
const qaAsking = ref(false)
const qaListEl = ref(null)
const embedLocal = ref(null) // 重跑索引的本地状态（宿主数据不含该字段轮询）
let embedTimer = null
const embedState = computed(() => embedLocal.value || props.video?.embed_status || 'none')
function embedStop() {
  embedTimer && clearInterval(embedTimer)
  embedTimer = null
}
function embedPollStart() {
  embedStop()
  const vid = props.video.vid
  embedTimer = setInterval(async () => {
    if (props.video?.vid !== vid) return embedStop()
    try {
      const v = (await api.getVideo(vid)).data
      if (v.embed_status === 'done' || v.embed_status === 'failed') {
        embedStop()
        embedLocal.value = v.embed_status
      }
    } catch {
      /* 单次轮询失败忽略 */
    }
  }, 5000)
}
function openQa() {
  tab.value = 'qa'
  if (embedState.value === 'running') embedPollStart() // 自动构建中：进来等它完成
}
async function qaReembed() {
  try {
    await api.reembed(props.video.vid)
  } catch (e) {
    return ui.error(e.message || '操作失败')
  }
  embedLocal.value = 'running'
  embedPollStart()
}
const qaAnswerHtml = a => marked.parse((a || '').replace(/</g, '&lt;'), { async: false })
// 毫秒时间戳 → HH:MM:SS（对话气泡时间戳，看模型回复延时用）
const fmtClock = ms => {
  const d = new Date(ms)
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map(n => String(n).padStart(2, '0'))
    .join(':')
}
async function qaAsk() {
  const q = qaInput.value.trim()
  if (!q || qaAsking.value || embedState.value !== 'done') return
  qaAsking.value = true
  qaInput.value = ''
  // 提问先上屏（a 空 = 气泡内显示加载），响应到达后原位填充——对齐 KnowledgeView 交互。
  // 整行替换而非 Object.assign：reactive 数组里是 proxy，改 push 前的 raw 对象不触发更新
  const idx = qaMessages.value.push({ q, ts: Date.now(), a: '', citations: [] }) - 1
  try {
    const history = qaMessages.value
      .slice(0, -1)
      .slice(-3)
      .map(m => ({ q: m.q, a: m.a }))
    const data = (await api.kbAsk({ question: q, history, scope: { vid: props.video.vid } })).data
    qaMessages.value[idx] = {
      ...qaMessages.value[idx],
      ats: Date.now(),
      ms: Date.now() - qaMessages.value[idx].ts,
      a: data.answer,
      rewritten: data.rewritten,
      citations: data.citations
    }
  } catch (e) {
    qaMessages.value.splice(idx, 1) // 失败撤回问题气泡
    ui.error(e.message || '问答失败')
    qaInput.value = q // 回滚输入，不吞问题
  } finally {
    qaAsking.value = false
  }
}
function qaClear() {
  qaMessages.value = []
}
// 新消息/加载态变化自动滚到底
watch(
  () => [qaMessages.value.length, qaAsking.value],
  () => nextTick(() => qaListEl.value?.scrollTo({ top: qaListEl.value.scrollHeight }))
)
onUnmounted(embedStop)

const exporting = ref(false)
async function exportClips(merge) {
  const pts = [...hlSelected.value].map(i => {
    const h = highlights.value[i]
    return { start: h.start, end: h.end }
  })
  if (!pts.length) return ui.info('请先勾选要导出的高光片段')
  exporting.value = true
  try {
    const body = await api.exportClips(props.video.vid, { points: pts, merge })
    const file = body.data.file
    const a = document.createElement('a')
    a.href = downloadHref(`/api/v1/media/${props.video.vid}/clip/${file}`)
    a.download = file
    a.click()
    ui.success('已开始下载')
  } catch (e) {
    ui.error(e.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

// 仅上传件可编辑标题/账号：douyin/bilibili/ytdlp 的标题账号是平台权威数据，不该改。
// uploader 影响采集记录页分组，上传件默认无账号，可在此补。
const isUpload = computed(() => props.video?.channel === 'upload')
const editing = ref(false)
const editTitle = ref('')
const editUploader = ref('')
const savingMeta = ref(false)

function openEdit() {
  editTitle.value = props.video?.title || ''
  editUploader.value = props.video?.uploader || ''
  editing.value = true
}
function cancelEdit() {
  editing.value = false
}
async function saveMeta() {
  const title = editTitle.value.trim()
  const uploader = editUploader.value.trim()
  if (!title && !uploader) return ui.info('标题和账号至少填一个')
  savingMeta.value = true
  try {
    await api.updateMeta(props.video.vid, { title: title || null, uploader: uploader || null })
    emit('meta', { vid: props.video.vid, title, uploader })
    editing.value = false
    ui.success('已保存')
  } catch (e) {
    ui.error(e.message || '保存失败')
  } finally {
    savingMeta.value = false
  }
}

const playing = computed(
  () => props.video && props.video.status === 'ready' && props.video.download_url
)

// 同一视频的轮询/重试更新会触发 watch，但不该重置播放器或切 tab——只在真正切换视频时才重置
const lastVid = ref(null)
watch(
  () => props.video,
  v => {
    if (v && lastVid.value === v.vid) return
    lastVid.value = v?.vid ?? null
    cur.value = 0
    hlStop()
    hlRunning.value = false
    hlPoints.value = []
    hlError.value = null
    hlSelected.value = new Set()
    dubStop()
    dubRunning.value = false
    dubError.value = null
    dubLocal.value = null
    dubMeta.value = null
    srtUrl.value = null
    langTarget.value = 'en'
    dubVoice.value = ''
    showDub.value = false
    subPairs.value = []
    embedStop()
    embedLocal.value = null
    qaMessages.value = []
    qaInput.value = ''
    tab.value = v?.ai_status === 'done' ? 'report' : 'transcript'
    // 引用跳转：等播放器渲染后 seek（seekChapter 内部已处理元数据未加载）
    if (v && props.initialSeek > 0) nextTick(() => seekChapter(props.initialSeek))
  },
  { immediate: true }
)

// 报告 Markdown → HTML。LLM 输出先转义尖括号，防注入
const reportHtml = computed(() => {
  if (!props.video?.ai_report) return ''
  const safe = props.video.ai_report.replace(/</g, '&lt;')
  return marked.parse(safe, { async: false })
})

// 当前播放位置对应的章节（高亮用）
const activeChapter = computed(() => {
  const chs = props.video?.ai_chapters || []
  let idx = -1
  chs.forEach((c, i) => {
    if (cur.value >= c.start) idx = i
  })
  return idx
})

// 章节/文案点击：跳转到对应视频进度并播放。
// 元数据未加载（readyState<1）时直接设 currentTime 会被浏览器忽略，等 loadedmetadata 再 seek
const seekChapter = start => {
  const el = videoEl.value
  if (!el) return
  const doSeek = () => {
    el.currentTime = start
    el.play().catch(() => {})
  }
  if (el.readyState >= 1) doSeek()
  else el.addEventListener('loadedmetadata', doSeek, { once: true })
}

// UTC 秒 → 本地时间串
const fmtDate = ts => {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

const copyLink = async () => {
  try {
    await navigator.clipboard.writeText(props.video.url)
    ui.success('链接已复制')
  } catch {
    ui.error('复制失败，请手动复制')
  }
}

const onKey = e => e.key === 'Escape' && emit('close')
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))

// 秒 → mm:ss（文案分段时间轴）
const fmtTs = s => {
  const t = Math.floor(s || 0)
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="video"
      class="fixed inset-0 z-modal bg-black/60 backdrop-blur-sm grid place-items-center p-4 sm:p-6"
      @click.self="emit('close')"
    >
      <div
        class="card w-full max-w-8xl h-[92vh] flex flex-col overflow-hidden animate-rise !rounded-2xl"
      >
        <!-- 顶栏：标题 + 关闭 -->
        <div class="flex items-start justify-between gap-3 p-4 sm:p-5 pb-3 shrink-0">
          <h2 class="font-display font-700 text-base sm:text-lg leading-snug text-ink line-clamp-2">
            {{ video.title || video.url }}
          </h2>
          <button
            class="w-8 h-8 shrink-0 rounded-full bg-surface-2 text-ink-faint grid place-items-center hover:text-ink hover:bg-line cursor-pointer transition-colors"
            title="关闭（Esc）"
            @click="emit('close')"
          >
            <span class="i-lucide-x" />
          </button>
        </div>

        <!-- 主体：左视频信息 / 右文案·报告 分栏 -->
        <div class="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
          <!-- 左列：播放器 + 元信息 + 操作 -->
          <div class="min-h-0 overflow-y-auto lg:border-r border-line">
            <div class="relative aspect-video bg-black">
              <video
                v-if="playing"
                ref="videoEl"
                :src="playerSrc"
                :poster="video.thumbnail"
                referrerpolicy="no-referrer"
                controls
                preload="metadata"
                class="w-full h-full"
                @timeupdate="cur = $event.target.currentTime"
              />
              <div v-else class="w-full h-full grid place-items-center text-ink-faint">
                <div class="text-center">
                  <span
                    v-if="statusMeta(video.status).active"
                    class="i-lucide-loader-circle animate-spin text-3xl"
                  />
                  <span
                    v-else-if="video.status === 'failed'"
                    class="i-lucide-circle-x text-3xl text-bad"
                  />
                  <span v-else class="i-lucide-video text-3xl" />
                  <p class="text-sm mt-2">{{ statusMeta(video.status).label }}</p>
                  <p v-if="video.error" class="text-xs text-bad mt-1 max-w-md mx-auto break-words">
                    {{ video.error }}
                  </p>
                </div>
              </div>
            </div>

            <!-- 时间轴高光条：总长比例 + 高亮点（按类型着色），悬停看理由，点击 seek -->
            <div v-if="highlights.length" class="px-4 sm:px-5 pt-4">
              <div class="flex items-center justify-between mb-1.5">
                <span class="text-xs text-ink-faint flex items-center gap-1">
                  <span class="i-lucide-zap" />时间轴高光段（{{ highlights.length }}）
                </span>
                <span class="flex gap-2.5 text-[10px] text-ink-faint">
                  <span
                    v-for="t in ['quote', 'conflict', 'concept', 'turn']"
                    :key="t"
                    class="flex items-center gap-1"
                  >
                    <i class="w-1.5 h-1.5 rounded-full" :class="hlMeta(t).dot" />{{
                      hlMeta(t).label
                    }}
                  </span>
                </span>
              </div>
              <div class="relative h-6 rounded-full bg-surface-2">
                <!-- 播放进度游标 -->
                <div
                  class="absolute top-0 bottom-0 w-px bg-ink/40"
                  :style="{ left: hlPct(cur) + '%' }"
                />
                <!-- 高亮区间段：start→end 整段着色，点击 seek 到起点 -->
                <button
                  v-for="(h, i) in highlights"
                  :key="i"
                  class="absolute top-1/2 -translate-y-1/2 h-3 rounded-full cursor-pointer transition-all hover:h-4"
                  :class="hlMeta(h.type).dot"
                  :style="{ left: hlPct(h.start) + '%', width: hlWidth(h) + '%' }"
                  :title="`${fmtTs(h.start)} - ${fmtTs(h.end)} · ${hlMeta(h.type).label}：${h.label}${h.reason ? '（' + h.reason + '）' : ''}`"
                  @click="seekChapter(h.start)"
                />
              </div>
            </div>

            <div class="p-4 sm:p-5 space-y-4">
              <!-- 元信息 -->
              <div class="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-faint">
                <span v-if="video.uploader" class="flex items-center gap-1">
                  <span class="i-lucide-user" />{{ video.uploader }}
                </span>
                <span v-if="video.extractor" class="chip !py-0.5">{{ video.extractor }}</span>
                <span v-if="video.duration" class="num flex items-center gap-1">
                  <span class="i-lucide-clock-3" />{{ formatDuration(video.duration) }}
                </span>
                <span v-if="video.size" class="num flex items-center gap-1">
                  <span class="i-lucide-hard-drive" />{{ formatBytes(video.size) }}
                </span>
                <span v-if="video.publish_at" class="flex items-center gap-1">
                  <span class="i-lucide-calendar" />发布于 {{ fmtDate(video.publish_at) }}
                </span>
                <span class="flex items-center gap-1">
                  <span class="i-lucide-history" />采集于 {{ video.created_at }}
                </span>
              </div>

              <!-- 操作 -->
              <div v-if="showDub" class="text-xs text-brand flex items-center gap-1">
                <span class="i-lucide-badge-check" />当前播放：配音版（译音替换原音 + 烧录译文字幕）
              </div>
              <div class="flex flex-wrap gap-2">
                <a
                  v-if="playing"
                  :href="downloadHref(video.download_url)"
                  :download="video.filename || ''"
                  class="btn-cta !h-9 !px-4 !text-sm no-underline"
                >
                  <span class="i-lucide-download" />保存到本地
                </a>

                <a
                  v-if="!isUpload"
                  :href="video.url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="btn-ghost !h-9 !px-4 !text-sm no-underline"
                >
                  <span class="i-lucide-external-link" />打开原视频
                </a>
                <button v-if="!isUpload" class="btn-ghost !h-9 !px-4 !text-sm" @click="copyLink">
                  <span class="i-lucide-link" />复制链接
                </button>
                <button
                  v-if="video.status === 'failed'"
                  class="btn-ghost !h-9 !px-4 !text-sm"
                  @click="emit('retry', video)"
                >
                  <span class="i-lucide-refresh-ccw" />重试
                </button>
                <button v-if="isUpload" class="btn-ghost !h-9 !px-4 !text-sm" @click="openEdit">
                  <span class="i-lucide-pencil" />编辑
                </button>
                <button
                  class="btn-ghost !h-9 !px-4 !text-sm hover:!text-bad"
                  @click="emit('remove', video)"
                >
                  <span class="i-lucide-trash-2" />删除
                </button>
              </div>
            </div>
          </div>

          <!-- 右列：文案 / AI 报告 tab -->
          <div class="min-h-0 flex flex-col border-t lg:border-t-0 border-line">
            <div class="flex gap-1 p-3 pb-0 shrink-0">
              <button
                class="tab-item"
                :class="tab === 'transcript' ? 'tab-active' : 'tab-inactive'"
                @click="tab = 'transcript'"
              >
                <span class="i-lucide-file-text mr-1" />视频文案
              </button>
              <button
                class="tab-item"
                :class="tab === 'qa' ? 'tab-active' : 'tab-inactive'"
                @click="openQa"
              >
                <span class="i-lucide-bot mr-1" />AI 问答
              </button>
              <button
                class="tab-item"
                :class="tab === 'report' ? 'tab-active' : 'tab-inactive'"
                @click="tab = 'report'"
              >
                <span class="i-lucide-sparkles mr-1" />AI 报告
              </button>
              <button
                class="tab-item"
                :class="tab === 'highlights' ? 'tab-active' : 'tab-inactive'"
                @click="tab = 'highlights'"
              >
                <span class="i-lucide-zap mr-1" />高光片段
                <span v-if="highlights.length" class="ml-0.5 text-xs"
                  >({{ highlights.length }})</span
                >
              </button>
              <button
                class="tab-item"
                :class="tab === 'subs' ? 'tab-active' : 'tab-inactive'"
                @click="openTranslate('subs')"
              >
                <span class="i-lucide-subtitles mr-1" />双语字幕
              </button>
              <button
                class="tab-item"
                :class="tab === 'dub' ? 'tab-active' : 'tab-inactive'"
                @click="openTranslate('dub')"
              >
                <span class="i-lucide-audio-lines mr-1" />AI 配音
              </button>
            </div>

            <div class="flex-1 min-h-0 overflow-y-auto p-4">
              <!-- ===== 文案 tab：逐句时间轴，点击跳进度 ===== -->
              <template v-if="tab === 'transcript'">
                <div
                  v-if="video.transcript"
                  class="text-xs leading-relaxed text-ink-soft space-y-1"
                >
                  <button
                    v-for="(seg, i) in video.transcript_segments"
                    :key="i"
                    class="w-full text-left flex gap-2.5 px-2 py-1.5 rounded-lg hover:bg-surface-1 cursor-pointer transition-colors"
                    @click="seekChapter(seg.start)"
                  >
                    <span class="num shrink-0 text-brand tabular-nums pt-px">{{
                      fmtTs(seg.start)
                    }}</span>
                    <span>{{ seg.text }}</span>
                  </button>
                  <p v-if="!video.transcript_segments?.length" class="whitespace-pre-wrap px-2">
                    {{ video.transcript }}
                  </p>
                </div>
                <div
                  v-else-if="video.transcript_error"
                  class="text-xs text-bad/90 flex items-start gap-1.5"
                >
                  <span class="i-lucide-file-warning shrink-0 mt-0.5" />
                  <span class="break-words flex-1">文案提取失败：{{ video.transcript_error }}</span>
                  <button
                    class="shrink-0 text-brand cursor-pointer hover:underline flex items-center gap-0.5"
                    title="基于本地成品重新转写"
                    @click="emit('retranscribe', video)"
                  >
                    <span class="i-lucide-rotate-ccw" />重新转写
                  </button>
                </div>
                <p v-else class="text-xs text-ink-faint flex items-center gap-1.5">
                  <span class="i-lucide-loader-circle animate-spin" />文案提取中…
                </p>
              </template>

              <!-- ===== 报告 tab：章节列表（当前章节高亮）+ Markdown ===== -->
              <template v-else-if="tab === 'report'">
                <div
                  v-if="video.ai_status === 'running'"
                  class="flex items-center gap-2 text-xs text-ink-faint py-2"
                >
                  <span class="i-lucide-loader-circle animate-spin" />AI
                  正在分析视频内容，约需十几秒…
                </div>
                <p
                  v-else-if="video.ai_status === 'failed'"
                  class="text-xs text-bad/90 flex items-start gap-1.5"
                >
                  <span class="i-lucide-file-warning shrink-0 mt-0.5" />
                  <span class="break-words flex-1">AI 分析失败：{{ video.ai_error }}</span>
                  <button
                    class="shrink-0 text-brand cursor-pointer hover:underline flex items-center gap-0.5"
                    title="基于现有文案重新生成 AI 报告"
                    @click="emit('reanalyze', video)"
                  >
                    <span class="i-lucide-rotate-ccw" />重试
                  </button>
                </p>
                <p v-else-if="video.ai_status !== 'done'" class="text-xs text-ink-faint py-2">
                  报告尚未生成
                </p>
                <div v-if="video.ai_status === 'done'" class="space-y-4">
                  <!-- 章节列表：时间+标题，hover 显示摘要 -->
                  <div class="space-y-1">
                    <button
                      v-for="(c, i) in video.ai_chapters"
                      :key="i"
                      class="w-full text-left flex items-center gap-2.5 px-2.5 py-2 rounded-lg cursor-pointer transition-colors"
                      :class="i === activeChapter ? 'bg-brand-soft' : 'hover:bg-surface-1'"
                      :title="c.summary"
                      @click="seekChapter(c.start)"
                    >
                      <span
                        class="num shrink-0 text-xs tabular-nums"
                        :class="i === activeChapter ? 'text-brand font-600' : 'text-ink-faint'"
                      >
                        {{ fmtTs(c.start) }}
                      </span>
                      <span
                        class="text-xs font-600 truncate"
                        :class="i === activeChapter ? 'text-brand' : 'text-ink'"
                      >
                        {{ c.title }}
                      </span>
                    </button>
                  </div>
                  <!-- 图文报告 -->
                  <div
                    class="prose-report text-sm leading-relaxed text-ink-soft bg-surface-1 rounded-xl p-4"
                    v-html="reportHtml"
                  />
                </div>
              </template>

              <!-- ===== 高光 tab：生成/重试 + 高亮点列表（勾选导出） ===== -->
              <template v-else-if="tab === 'highlights'">
                <div
                  v-if="hlStatus === 'running'"
                  class="flex items-center gap-2 text-xs text-ink-faint py-2"
                >
                  <span class="i-lucide-loader-circle animate-spin" />AI 正在标注高光点，约需十几秒…
                </div>
                <p
                  v-else-if="hlStatus === 'failed'"
                  class="text-xs text-bad/90 flex items-start gap-1.5"
                >
                  <span class="i-lucide-file-warning shrink-0 mt-0.5" />
                  <span class="break-words flex-1"
                    >高光标注失败：{{ hlError || video.highlight_error }}</span
                  >
                  <button
                    class="shrink-0 text-brand cursor-pointer hover:underline flex items-center gap-0.5"
                    @click="genHighlights"
                  >
                    <span class="i-lucide-rotate-ccw" />重试
                  </button>
                </p>
                <!-- 未标注：居中空态 + CTA（对齐项目空态惯例；需转写完成） -->
                <div
                  v-else-if="!highlights.length"
                  class="flex flex-col items-center justify-center text-center gap-3 py-10"
                >
                  <p class="text-xs text-ink-faint max-w-[22rem] leading-relaxed">
                    {{
                      video.transcript
                        ? '标出时间轴上的高光点（金句 / 冲突 / 核心概念 / 转折），便于快速回看与切片导出'
                        : '文案转写完成后可生成高光标注'
                    }}
                  </p>
                  <button
                    v-if="video.transcript"
                    class="btn-primary !h-9 !px-5 !text-sm"
                    @click="genHighlights"
                  >
                    <span class="i-lucide-zap" />生成高光标注
                  </button>
                </div>
                <!-- 已标注：列表 + 勾选 + 导出 -->
                <div v-else class="space-y-3">
                  <div class="flex items-center justify-between">
                    <button
                      class="text-xs text-brand cursor-pointer hover:underline flex items-center gap-1"
                      @click="hlToggleAll"
                    >
                      <span
                        :class="hlSelected.size ? 'i-lucide-check-square' : 'i-lucide-square'"
                      />{{ hlSelected.size === highlights.length ? '取消全选' : '全选' }}
                    </button>
                    <div class="flex gap-2">
                      <button
                        class="btn-ghost !h-8 !px-3 !text-xs"
                        :disabled="exporting"
                        title="每个片段单独导出，打包成 zip"
                        @click="exportClips(false)"
                      >
                        <span
                          :class="
                            exporting ? 'i-lucide-loader-circle animate-spin' : 'i-lucide-scissors'
                          "
                        />分条导出
                      </button>
                      <button
                        class="btn-ghost !h-8 !px-3 !text-xs"
                        :disabled="exporting"
                        title="按时间顺序拼成一个高光集锦 mp4"
                        @click="exportClips(true)"
                      >
                        <span
                          :class="
                            exporting
                              ? 'i-lucide-loader-circle animate-spin'
                              : 'i-lucide-clapperboard'
                          "
                        />合并导出
                      </button>
                    </div>
                  </div>
                  <div class="space-y-1.5">
                    <div
                      v-for="(h, i) in highlights"
                      :key="i"
                      class="flex items-start gap-2.5 px-2.5 py-2 rounded-lg hover:bg-surface-1 transition-colors"
                    >
                      <button
                        class="shrink-0 mt-0.5 text-ink-faint hover:text-brand cursor-pointer"
                        :class="hlSelected.has(i) ? 'text-brand' : ''"
                        @click="hlToggle(i)"
                      >
                        <span
                          :class="hlSelected.has(i) ? 'i-lucide-check-square' : 'i-lucide-square'"
                        />
                      </button>
                      <button
                        class="num shrink-0 text-xs tabular-nums text-brand pt-0.5 cursor-pointer hover:underline"
                        title="跳转到片段起点"
                        @click="seekChapter(h.start)"
                      >
                        {{ fmtTs(h.start)
                        }}<span class="text-ink-faint"> → {{ fmtTs(h.end) }}</span>
                      </button>
                      <div class="min-w-0 flex-1">
                        <div class="flex items-center gap-1.5">
                          <span
                            class="text-[10px] px-1.5 py-0.5 rounded-full shrink-0"
                            :class="[hlMeta(h.type).text, hlMeta(h.type).soft]"
                          >
                            {{ hlMeta(h.type).label }}
                          </span>
                          <span class="text-xs font-600 text-ink truncate">{{ h.label }}</span>
                        </div>
                        <p v-if="h.reason" class="text-xs text-ink-faint mt-0.5 leading-relaxed">
                          {{ h.reason }}
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              </template>

              <!-- ===== 字幕 tab：选语言生成双语字幕 + 滚动预览 ===== -->
              <template v-else-if="tab === 'subs'">
                <div class="flex flex-col h-full min-h-0 gap-3">
                  <p class="text-xs text-ink-faint shrink-0">
                    源语言：
                    <span class="text-ink font-600">{{
                      langs[video.lang_source]?.name || '自动识别'
                    }}</span>
                    <span v-if="!video.lang_source">（未检出，LLM 翻译时自行识别原文）</span>
                    <span v-else>（转写后自动检测）</span>
                  </p>

                  <p v-if="!video.transcript" class="text-xs text-ink-faint">
                    文案转写完成后可翻译。
                  </p>

                  <template v-else>
                    <div class="flex items-center gap-2 flex-wrap shrink-0">
                      <span class="text-xs text-ink-faint shrink-0">翻译成</span>
                      <button
                        v-for="(info, code) in langs"
                        :key="code"
                        class="seg-item"
                        :class="langTarget === code ? 'seg-active' : 'seg-inactive'"
                        :disabled="code === video.lang_source"
                        :title="code === video.lang_source ? '视频源语言，无需翻译' : ''"
                        @click="langTarget = code"
                      >
                        {{ info.name }}
                      </button>
                      <button
                        class="btn-primary !h-7 !px-3.5 !text-xs ml-2"
                        :disabled="translating"
                        @click="genSrt"
                      >
                        <span
                          :class="
                            translating
                              ? 'i-lucide-loader-circle animate-spin'
                              : 'i-lucide-subtitles'
                          "
                        />
                        {{ srtReady ? '重新生成' : '生成双语字幕' }}
                      </button>
                      <a
                        v-if="srtReady"
                        :href="downloadHref(`/api/v1/media/${video.vid}/file?kind=sub`)"
                        class="text-xs text-brand cursor-pointer hover:underline flex items-center gap-1 no-underline"
                      >
                        <span class="i-lucide-download" />下载 srt
                      </a>
                    </div>

                    <!-- 字幕预览：独立滚动，点时间跳播放 -->
                    <div
                      v-if="subLoading && !subPairs.length"
                      class="text-xs text-ink-faint flex items-center gap-1.5"
                    >
                      <span class="i-lucide-loader-circle animate-spin" />字幕加载中…
                    </div>
                    <div
                      v-else-if="subPairs.length"
                      class="flex-1 min-h-0 overflow-y-auto bg-surface-1 rounded-xl p-3 space-y-1"
                    >
                      <div
                        v-for="(p, i) in subPairs"
                        :key="i"
                        class="flex gap-2.5 px-2 py-1.5 rounded-lg hover:bg-surface-2 transition-colors"
                      >
                        <button
                          class="num shrink-0 text-xs tabular-nums text-brand pt-0.5 cursor-pointer hover:underline"
                          @click="seekChapter(p.start)"
                        >
                          {{ fmtTs(p.start) }}
                        </button>
                        <div class="min-w-0 flex-1">
                          <p class="text-xs text-ink leading-relaxed break-words">{{ p.target }}</p>
                          <p
                            v-if="p.source"
                            class="text-[11px] text-ink-faint leading-relaxed break-words mt-0.5"
                          >
                            {{ p.source }}
                          </p>
                        </div>
                      </div>
                    </div>
                    <p v-else-if="srtReady" class="text-xs text-ink-faint">
                      字幕已生成，点「重新生成」可预览译文。
                    </p>
                    <p v-else class="text-xs text-ink-faint">生成后在此预览双语译文。</p>
                  </template>
                </div>
              </template>

              <!-- ===== 配音 tab：选音色生成配音视频（须先有字幕） ===== -->
              <template v-else-if="tab === 'dub'">
                <div class="space-y-3.5">
                  <p v-if="!video.transcript" class="text-xs text-ink-faint">
                    文案转写完成后可配音。
                  </p>

                  <template v-else>
                    <p v-if="!srtReady" class="text-xs text-warn flex items-center gap-1 flex-wrap">
                      <span class="i-lucide-lock" />配音前需先生成并确认双语字幕
                      <button
                        class="text-brand cursor-pointer hover:underline"
                        @click="openTranslate('subs')"
                      >
                        去「字幕」tab 生成
                      </button>
                    </p>

                    <p class="text-xs text-ink-faint">
                      配音语言：
                      <span class="text-ink font-600">{{ langs[langTarget]?.name || '—' }}</span>
                      <span class="text-ink-faint">（跟随「双语字幕」）</span>
                    </p>
                    <div
                      class="flex items-center gap-2 flex-wrap"
                      :class="srtReady ? '' : 'opacity-55 pointer-events-none'"
                    >
                      <span class="text-xs text-ink-faint shrink-0">配音音色</span>
                      <button
                        v-for="v in targetVoices"
                        :key="v.id"
                        class="seg-item"
                        :class="effectiveVoice === v.id ? 'seg-active' : 'seg-inactive'"
                        @click="dubVoice = v.id"
                      >
                        {{ v.label }}
                      </button>
                      <button
                        class="btn-primary !h-7 !px-3.5 !text-xs ml-2"
                        :disabled="dubRunning"
                        @click="genDub"
                      >
                        <span
                          :class="
                            dubRunning
                              ? 'i-lucide-loader-circle animate-spin'
                              : 'i-lucide-audio-lines'
                          "
                        />
                        {{ dubState === 'done' ? '重新生成配音' : '生成配音视频' }}
                      </button>
                    </div>
                    <p class="text-xs text-ink-faint leading-relaxed">
                      译音逐句合成并对齐原句时长，替换原音轨；画面底部烧入译文字幕。耗时约数分钟。
                    </p>

                    <div
                      v-if="dubState === 'running'"
                      class="flex items-center gap-2 text-xs text-ink-faint"
                    >
                      <span
                        class="i-lucide-loader-circle animate-spin"
                      />配音合成中，可关掉弹窗稍后回来看…
                    </div>
                    <p v-else-if="dubState === 'failed'" class="text-xs text-bad/90 break-words">
                      配音失败：{{ dubError || video.dub_error }}
                    </p>
                    <div v-if="dubState === 'done'" class="flex items-center gap-2 flex-wrap">
                      <button
                        v-if="playing"
                        class="btn-ghost !h-8 !px-3.5 !text-xs"
                        :title="showDub ? '切回原始音轨' : '左侧播放器切换配音版，续播当前进度'"
                        @click="toggleDubSource"
                      >
                        <span :class="showDub ? 'i-lucide-video' : 'i-lucide-audio-lines'" />
                        {{ showDub ? '切原视频' : '切配音视频' }}
                      </button>
                      <a
                        :href="dubFileHref"
                        download
                        class="text-xs text-brand cursor-pointer hover:underline flex items-center gap-1 no-underline"
                      >
                        <span class="i-lucide-download" />下载
                      </a>
                    </div>
                    <!-- 当前配音产物（读表/本地轮询结果）：语言 + 音色 -->
                    <p v-if="dubState === 'done' && dubLangEff" class="text-xs text-ink-faint">
                      当前配音：{{ langs[dubLangEff]?.name || dubLangEff }} · {{ dubVoiceLabel }}
                    </p>
                  </template>
                </div>
              </template>

              <!-- ===== 问答 tab：针对本视频的 RAG 对话 ===== -->
              <template v-else>
                <!-- 索引门控：正常由转写链自动构建，仅等待/失败/存量未索引需干预 -->
                <div
                  v-if="embedState === 'running'"
                  class="flex flex-col items-center gap-3 py-12 text-center"
                >
                  <span class="i-lucide-loader-circle animate-spin text-2xl text-brand" />
                  <p class="text-xs text-ink-faint">正在为本视频构建向量索引，完成后即可提问…</p>
                </div>
                <div
                  v-else-if="embedState !== 'done'"
                  class="flex flex-col items-center justify-center text-center gap-3 py-12"
                >
                  <div class="w-12 h-12 rounded-2xl bg-surface-1 grid place-items-center">
                    <span class="i-lucide-database text-2xl text-brand opacity-50" />
                  </div>
                  <p class="text-xs text-ink-faint max-w-[22rem] leading-relaxed break-words">
                    {{
                      video.embed_error
                        ? `索引构建失败：${video.embed_error}`
                        : '本视频尚未建立向量索引，问答需要基于索引检索内容片段。'
                    }}
                  </p>
                  <button class="btn-primary !h-9 !px-5 !text-sm" @click="qaReembed">
                    <span class="i-lucide-refresh-ccw" />构建索引
                  </button>
                </div>
                <!-- 问答面板：消息列表滚动 + 底部固定输入 -->
                <div v-else class="flex flex-col h-full min-h-0">
                  <div ref="qaListEl" class="flex-1 min-h-0 overflow-y-auto space-y-3 pr-1 pb-2">
                    <p
                      v-if="!qaMessages.length && !qaAsking"
                      class="text-xs text-ink-faint py-8 text-center leading-relaxed"
                    >
                      就本视频提问，例如：这条视频的核心观点是什么？
                    </p>
                    <template v-for="(m, i) in qaMessages" :key="i">
                      <div class="flex flex-col items-end gap-1">
                        <span class="num text-[10px] text-ink-faint/60 tabular-nums px-1">{{
                          fmtClock(m.ts)
                        }}</span>
                        <div
                          class="max-w-[85%] rounded-2xl rounded-br-md bg-brand text-white px-3.5 py-2 text-xs leading-relaxed whitespace-pre-wrap break-words"
                        >
                          {{ m.q }}
                        </div>
                      </div>
                      <div>
                        <div
                          class="text-[10px] text-ink-faint/60 mb-1 flex items-center gap-1.5 px-1"
                        >
                          <span v-if="m.ats" class="num tabular-nums">{{ fmtClock(m.ats) }}</span>
                          <span v-if="m.ms != null" class="num"
                            >· 用时 {{ (m.ms / 1000).toFixed(1) }}s</span
                          >
                        </div>
                        <p
                          v-if="m.rewritten && m.rewritten !== m.q"
                          class="text-xs text-ink-faint mb-1 flex items-center gap-1"
                        >
                          <span class="i-lucide-wand-sparkles" />按「{{ m.rewritten }}」检索
                        </p>
                        <div
                          class="max-w-[92%] rounded-2xl rounded-bl-md bg-surface-1 px-3.5 py-2.5"
                        >
                          <div
                            v-if="m.a"
                            class="prose-report text-xs leading-relaxed"
                            v-html="qaAnswerHtml(m.a)"
                          />
                          <div v-else class="flex items-center gap-2 text-xs text-ink-faint py-0.5">
                            <span
                              class="i-lucide-loader-circle animate-spin"
                            />正在检索片段并生成回答…
                          </div>
                          <div
                            v-if="m.citations?.length"
                            class="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-line"
                          >
                            <button
                              v-for="c in m.citations"
                              :key="c.n"
                              class="chip !py-0.5 cursor-pointer hover:!border-brand hover:!text-brand transition-colors"
                              :title="c.snippet"
                              @click="seekChapter(c.start)"
                            >
                              <span class="num text-brand font-600">[{{ c.n }}]</span>
                              <span class="max-w-[16rem] truncate">{{ c.snippet }}</span>
                              <span class="num tabular-nums">{{ fmtTs(c.start) }}</span>
                            </button>
                          </div>
                        </div>
                      </div>
                    </template>
                  </div>
                  <div class="shrink-0 flex gap-2 pt-3 border-t border-line">
                    <input
                      v-model="qaInput"
                      class="field !h-9 text-sm"
                      placeholder="关于本视频的问题，回车提问…"
                      @keydown.enter="qaAsk"
                    />
                    <button
                      v-if="qaMessages.length"
                      class="w-9 h-9 shrink-0 grid place-items-center rounded-lg border border-line text-ink-faint hover:text-brand hover:border-brand cursor-pointer transition-colors"
                      title="清空对话，重新开始"
                      @click="qaClear"
                    >
                      <span class="i-lucide-message-square-plus" />
                    </button>
                    <button
                      class="btn-primary !h-9 !px-4 !text-sm shrink-0"
                      :disabled="qaAsking || !qaInput.trim()"
                      @click="qaAsk"
                    >
                      <span class="i-lucide-send" />提问
                    </button>
                  </div>
                </div>
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 编辑标题/账号弹窗（仅上传件） -->
    <div
      v-if="editing"
      class="fixed inset-0 z-modal grid place-items-center p-4 bg-black/45 backdrop-blur-sm"
      @click.self="cancelEdit"
    >
      <div class="card w-full max-w-sm p-5 animate-rise">
        <h3 class="text-sm font-600 text-ink mb-3">编辑信息</h3>
        <label class="block text-xs text-ink-faint mb-1">标题</label>
        <input v-model="editTitle" placeholder="视频标题" class="field !h-9 !text-sm mb-3" />
        <label class="block text-xs text-ink-faint mb-1">账号名</label>
        <input
          v-model="editUploader"
          placeholder="用于采集记录分组，可留空"
          class="field !h-9 !text-sm"
        />
        <div class="flex justify-end gap-2 mt-5">
          <button class="btn-ghost !h-9 !px-4 !text-sm" @click="cancelEdit">取消</button>
          <button class="btn-primary !h-9 !px-4 !text-sm" :disabled="savingMeta" @click="saveMeta">
            <span
              :class="savingMeta ? 'i-lucide-loader-circle animate-spin' : 'i-lucide-check'"
            />保存
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped></style>
