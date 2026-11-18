<script setup>
import api from '@/api'
import { parseUrls, statusMeta } from '@/utils'

const ui = useUiStore()

const text = ref('')
const probing = ref(false)
const items = ref([]) // 采集记录列表，按 vid 轮询更新
let pollTimer = null

// 输入方式：贴链接 / 上传文件（二选一 tab）
const inputMode = ref('link') // link | file
const fileInput = ref(null)
const uploading = ref(false)
const dragOver = ref(false) // 拖拽悬停高亮
const uploadPct = ref(0) // 当前文件上传百分比
const uploadQueue = ref({ done: 0, total: 0 }) // 多文件队列进度
const MAX_UPLOAD = 500 * 1024 * 1024 // 500MB，前端拦

function onFiles(e) {
  handleFiles(e.target.files)
  e.target.value = '' // 复位，允许重选同一文件
}

function onDrop(e) {
  dragOver.value = false
  handleFiles(e.dataTransfer.files)
}

async function handleFiles(fileList) {
  if (uploading.value) return
  const files = [...fileList]
  if (!files.length) return
  const valid = []
  for (const f of files) {
    if (!f.type.startsWith('video/')) {
      ui.error(`「${f.name}」不是视频文件`)
      continue
    }
    if (f.size > MAX_UPLOAD) {
      ui.error(`「${f.name}」超过 500MB`)
      continue
    }
    valid.push(f)
  }
  if (!valid.length) return
  uploading.value = true
  uploadQueue.value = { done: 0, total: valid.length }
  for (const f of valid) {
    uploadPct.value = 0
    await doUpload(f)
    uploadQueue.value.done++
  }
  uploading.value = false
  uploadPct.value = 0
}

async function doUpload(file) {
  try {
    const form = new FormData()
    form.append('file', file)
    const body = await api.upload(form, p => (uploadPct.value = p))
    const { deduped, ...video } = body.data
    const created = { ...video, ok: true } // 对齐 probe 项结构（MediaCard/activeIds 依赖 ok）
    items.value = [created, ...items.value.filter(i => i.vid !== created.vid)]
    if (deduped) ui.info(`「${file.name}」内容已采集过，已置顶`)
    ensurePolling() // 上传即 ready，轮询等转写/报告回填
  } catch (e) {
    ui.error(`「${file.name}」${e.message || '上传失败'}`)
  }
}

async function onFetch(input) {
  const src = input ?? text.value
  const urls = parseUrls(src)
  if (!urls.length) return
  probing.value = true
  try {
    const body = await api.probe({ urls })
    const created = body.data
    // 新采集置顶；按 vid（失败项按 url）去重覆盖旧记录
    const keyOf = i => i.vid || i.url
    const createdKeys = new Set(created.map(keyOf))
    items.value = [...created, ...items.value.filter(i => !createdKeys.has(keyOf(i)))]
    if (created.every(c => !c.ok)) ui.error('链接解析失败，请检查地址是否有效')
    ensurePolling()
  } catch (e) {
    ui.error(e.message || '解析失败')
  } finally {
    probing.value = false
  }
}

// ---- 轮询：有活动采集才跑（下载完成后转写中的记录也继续轮询到 transcript 出结果） ----
// 判据必须用 transcript_len：轮询接口是 lite 数据，永远不含 transcript 正文（全量要点开详情/补拉才有）
const activeIds = () =>
  items.value
    .filter(
      i =>
        i.vid &&
        i.ok &&
        (statusMeta(i.status).active ||
          (i.status === 'ready' && !i.transcript_len && !i.transcript_error) ||
          (i.status === 'ready' && i.ai_status === 'running'))
    )
    .map(i => i.vid)

function ensurePolling() {
  if (!pollTimer) pollTimer = setInterval(pollOnce, 5000)
}
function maybeStopPolling() {
  if (pollTimer && !activeIds().length) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}
async function pollOnce() {
  const ids = activeIds()
  if (!ids.length) return maybeStopPolling()
  try {
    const body = await api.pollVideos(ids)
    const map = Object.fromEntries(body.data.map(v => [v.vid, v]))
    items.value = items.value.map(it => (it.vid && map[it.vid] ? { ...it, ...map[it.vid] } : it))
    // 转写刚完成：lite 无正文，补拉一次全量让卡片「查看文案」可用（每条只拉一次，拉完 transcript 在场不再触发）
    for (const it of items.value) {
      if (it.vid && map[it.vid]?.transcript_len && !it.transcript) {
        api
          .getVideo(it.vid)
          .then(b => {
            const i = items.value.findIndex(x => x.vid === it.vid)
            if (i >= 0) items.value[i] = { ...items.value[i], ...b.data }
          })
          .catch(() => {})
      }
    }
  } catch {
    /* 单次失败忽略 */
  }
  maybeStopPolling()
}
onUnmounted(() => pollTimer && clearInterval(pollTimer))

// ---- 采集动作 ----
async function onRetry(url) {
  await onFetch(url)
}

async function onRetranscribe(vid) {
  try {
    await api.retranscribe(vid)
    const it = items.value.find(i => i.vid === vid)
    if (it) it.transcript_error = null // 清错误回到「提取中」，轮询接管后续状态
    ensurePolling()
    ui.info('已重新提交转写')
  } catch (e) {
    ui.error(e.message || '操作失败')
  }
}

async function onRemove(vid) {
  try {
    await api.removeVideo(vid)
    items.value = items.value.filter(i => i.vid !== vid)
    ui.info('已删除')
  } catch (e) {
    ui.error(e.message)
  }
}

function clearAll() {
  items.value = []
}
</script>

<template>
  <div class="page">
    <header class="mb-6">
      <h1 class="font-display text-2xl font-800 tracking-tight">视频采集</h1>
      <p class="text-dim text-sm mt-1">
        粘贴链接自动探测下载，或上传本地视频文件；完成后进入转写 → AI 报告 → 知识库索引。
      </p>
    </header>

    <!-- 输入方式切换：贴链接 / 上传文件 -->
    <div class="flex gap-1 p-1 bg-surface-2 rounded-xl w-fit mb-4">
      <button
        class="px-4 h-8 rounded-lg text-sm cursor-pointer transition-colors flex items-center gap-1"
        :class="
          inputMode === 'link'
            ? 'bg-white shadow-sm font-600 text-ink'
            : 'text-ink-faint hover:text-ink'
        "
        @click="inputMode = 'link'"
      >
        <span class="i-lucide-link" />贴链接
      </button>
      <button
        class="px-4 h-8 rounded-lg text-sm cursor-pointer transition-colors flex items-center gap-1"
        :class="
          inputMode === 'file'
            ? 'bg-white shadow-sm font-600 text-ink'
            : 'text-ink-faint hover:text-ink'
        "
        @click="inputMode = 'file'"
      >
        <span class="i-lucide-upload" />上传文件
      </button>
    </div>

    <UrlComposer
      v-if="inputMode === 'link'"
      v-model:text="text"
      :loading="probing"
      @fetch="onFetch"
    />

    <!-- 上传文件：覆盖无法下载的视频，直落库进转写/报告/索引；支持点击选择或拖拽 -->
    <div v-else class="card p-4 sm:p-5">
      <div
        class="border-2 border-dashed rounded-xl px-6 py-10 text-center cursor-pointer transition-colors"
        :class="dragOver ? 'border-brand bg-brand-soft/40' : 'border-line hover:border-brand/50'"
        @click="fileInput?.click()"
        @dragover.prevent="dragOver = true"
        @dragleave.prevent="dragOver = false"
        @drop.prevent="onDrop"
      >
        <span
          :class="uploading ? 'i-lucide-loader-circle animate-spin' : 'i-lucide-upload-cloud'"
          class="text-3xl text-ink-faint/50"
        />
        <p class="text-sm text-ink-soft mt-3">
          {{ uploading ? '上传中…' : dragOver ? '松开即上传' : '点击选择或拖拽视频文件到此处' }}
        </p>
        <p class="text-xs text-ink-faint mt-1">
          支持本地视频文件，单个 ≤ 500MB；用于覆盖无法在线下载的视频
        </p>
        <!-- 上传进度：当前文件百分比 + 多文件队列 -->
        <div v-if="uploading" class="max-w-sm mx-auto mt-4">
          <div class="h-1.5 rounded-full bg-surface-2 overflow-hidden">
            <div
              class="h-full bg-brand rounded-full transition-[width] duration-150"
              :style="{ width: uploadPct + '%' }"
            />
          </div>
          <p class="text-xs text-ink-faint mt-1.5 num">
            {{ uploadPct }}%
            <template v-if="uploadQueue.total > 1">
              · 第 {{ uploadQueue.done + 1 }}/{{ uploadQueue.total }} 个</template
            >
          </p>
        </div>
      </div>
      <input
        ref="fileInput"
        type="file"
        accept="video/*"
        multiple
        class="hidden"
        @change="onFiles"
      />
    </div>

    <section v-if="items.length" class="mt-8">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-sm text-dim flex items-center gap-2">
          共 <span class="num text-ink font-medium">{{ items.length }}</span> 条
          <span v-if="activeIds().length" class="text-brand"
            >· <span class="num">{{ activeIds().length }}</span> 个处理中</span
          >
        </h2>
        <button
          class="text-xs text-ink-faint hover:text-ink cursor-pointer transition-colors flex items-center gap-1"
          @click="clearAll"
        >
          <span class="i-lucide-trash-2" />清空列表
        </button>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        <MediaCard
          v-for="it in items"
          :key="it.vid || it.url"
          :item="it"
          @retry="onRetry"
          @remove="onRemove"
          @retranscribe="onRetranscribe"
        />
      </div>
    </section>

    <section v-else class="mt-16 text-center animate-fade-in">
      <div
        class="mx-auto w-16 h-16 rounded-2xl bg-white border border-line grid place-items-center text-ink-faint/50 mb-4"
      >
        <span class="i-lucide-clapperboard text-3xl" />
      </div>
      <p class="text-ink-faint text-sm">采集结果会显示在这里</p>
    </section>
  </div>
</template>
