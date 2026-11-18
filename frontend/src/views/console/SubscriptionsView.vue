<script setup>
import api from '@/api'
import { fromNow } from '@/utils'
import ConfirmDialog from '@/components/ConfirmDialog.vue'

const ui = useUiStore()

function fmtNum(n) {
  if (n == null) return ''
  if (n >= 10000) return (n / 10000).toFixed(n % 10000 === 0 ? 0 : 1) + '万'
  return String(n)
}

const subs = ref([])
const loading = ref(false) // 首屏
const adding = ref(false)
const addUrl = ref('')
let pollTimer = null

// 轮询只在有进行中的检查时开启（采集一轮 1~3 分钟），否则停轮询省流量。
// 检查是后台协程，前端不维护"检查中"伪状态——重复点击由后端重入闸挡下并给出提示。
async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const data = (await api.subList()).data
    subs.value = (data?.subs ?? []).map(s => ({ ...s, reprobing: false }))
  } catch (e) {
    if (!silent) ui.error(e.message || '加载失败')
  } finally {
    loading.value = false
    maybeStopPolling() // 检查全部完成后停轮询
  }
}

async function onAdd() {
  const url = addUrl.value.trim()
  if (!url) return ui.info('请粘贴频道或作者主页链接')
  adding.value = true
  try {
    const body = await api.subCreate({ url })
    const { probe, ...sub } = body.data // 创建即带回探测到的账号信息
    subs.value = [sub, ...subs.value.filter(s => s.sid !== sub.sid)]
    addUrl.value = ''
    const total = probe?.total != null ? ` · 共 ${probe.total} 条视频` : ''
    const fans = probe?.follower_count != null ? ` · 粉丝 ${fmtNum(probe.follower_count)}` : ''
    ui.success(
      `已订阅「${sub.name || sub.extractor || '该频道'}」${total}${fans}，点击「立即采集」开始采集`
    )
  } catch (e) {
    ui.error(e.message || '添加失败')
  } finally {
    adding.value = false
  }
}

// 定时采集时间 + 每轮条数是同一份订阅偏好，合并成一个设置接口；任一控件改动都整份提交
async function saveSettings(s, patch, ev) {
  const hour = 'hour' in patch ? patch.hour : s.check_hour
  const limit = 'limit' in patch ? patch.limit : s.enum_limit
  if (!Number.isInteger(limit) || limit < 1 || limit > 50) {
    if (ev) ev.target.value = s.enum_limit // 非法条数回滚
    return ui.info('条数请填 1-50')
  }
  try {
    const body = await api.subSettings(s.sid, { hour, limit })
    const i = subs.value.findIndex(x => x.sid === s.sid)
    if (i >= 0) subs.value[i] = { ...subs.value[i], ...body.data } // 合并，保留 collected_count/reprobing
    ui.info('已保存设置')
  } catch (e) {
    ui.error(e.message || '设置失败')
    if (ev) ev.target.value = 'hour' in patch ? (s.check_hour ?? '') : s.enum_limit // 回滚
  }
}

function onSchedule(s, ev) {
  const v = ev.target.value
  saveSettings(s, { hour: v === '' ? null : Number(v) }, ev)
}

function onLimit(s, ev) {
  saveSettings(s, { limit: Number(ev.target.value) }, ev)
}

async function onCheck(s) {
  s.checking = true
  try {
    const body = await api.subCheck(s.sid) // 条数用后端存的 enum_limit（与定时共享）
    ui.info(body.msg || '检查已启动')
    ensurePolling() // 采集一轮 1~3 分钟，开始轮询等回填
  } catch (e) {
    s.checking = false
    ui.error(e.message || '启动检查失败')
  }
}

async function onReprobe(s) {
  const i = subs.value.findIndex(x => x.sid === s.sid)
  if (i < 0) return
  subs.value[i].reprobing = true
  try {
    const body = await api.subReprobe(s.sid)
    if (i >= 0) subs.value[i] = { ...subs.value[i], ...body.data.sub }
    ui.success('已刷新账号信息')
  } catch (e) {
    ui.error(e.message || '刷新失败')
  } finally {
    if (i >= 0) subs.value[i].reprobing = false
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

async function onRemove(s) {
  const ok = await confirmDialog({
    title: '取消订阅',
    message: `确定取消订阅「${s.name || s.extractor || '该频道'}」？\n已采集的视频保留，仅移除订阅关系。`,
    confirmText: '取消订阅',
    cancelText: '保留',
    danger: true
  })
  if (!ok) return
  try {
    await api.subRemove(s.sid)
    subs.value = subs.value.filter(x => x.sid !== s.sid)
    ui.success('已取消订阅')
  } catch (e) {
    ui.error(e.message)
  }
}

// 只有存在进行中的检查时才轮询（采集一轮 1~3 分钟），否则停轮询省流量
function hasChecking() {
  return subs.value.some(s => s.checking)
}

function ensurePolling() {
  if (pollTimer) return
  pollTimer = setInterval(() => load(true), 10000)
}
function maybeStopPolling() {
  if (pollTimer && !hasChecking()) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(async () => {
  await load()
  if (hasChecking()) ensurePolling()
})
onUnmounted(() => pollTimer && clearInterval(pollTimer))
</script>

<template>
  <div class="page">
    <header class="mb-6">
      <h1 class="font-display text-2xl font-800 tracking-tight">订阅账号</h1>
      <p class="text-dim text-sm mt-1">
        关注创作者：粘贴频道主页链接，自动发现新视频并进入「下载 → 转写 → 报告 →
        索引」流水线；默认不自动检查，也可为每个订阅设定每日定点时间
      </p>
    </header>

    <!-- 添加订阅 -->
    <div class="card p-4 mb-6 flex gap-2 flex-wrap sm:flex-nowrap">
      <input
        v-model="addUrl"
        type="text"
        class="flex-1 h-11 px-4 rounded-xl bg-white border border-line text-sm outline-none focus:border-brand transition-colors min-w-[16rem]"
        placeholder="抖音作者主页 / YouTube 频道 Videos 页 / B站空间 等链接"
        @keyup.enter="onAdd"
      />
      <button class="btn-primary !h-11 shrink-0" :disabled="adding" @click="onAdd">
        <span :class="adding ? 'i-lucide-loader-circle animate-spin' : 'i-lucide-plus'" />添加订阅
      </button>
    </div>

    <!-- 列表 -->
    <div
      v-if="loading && !subs.length"
      class="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-3"
    >
      <div v-for="i in 10" :key="i" class="card p-3 flex flex-col gap-2 animate-pulse">
        <div class="flex items-start gap-2.5">
          <div class="w-9 h-9 rounded-lg bg-surface-2 shrink-0" />
          <div class="flex-1 space-y-1.5">
            <div class="h-4 w-3/5 rounded bg-surface-2" />
            <div class="h-3 w-2/5 rounded bg-surface-2" />
          </div>
        </div>
        <div class="h-3 w-full rounded bg-surface-2" />
        <div class="h-3 w-4/5 rounded bg-surface-2" />
        <div class="h-7 w-full rounded bg-surface-2 mt-auto" />
      </div>
    </div>
    <div
      v-else-if="!subs.length"
      class="card flex flex-col items-center justify-center py-24 text-ink-faint gap-4 animate-fade-in"
    >
      <div class="w-16 h-16 rounded-2xl bg-surface-1 grid place-items-center">
        <span class="i-lucide-rss text-3xl opacity-40" />
      </div>
      <div class="text-center">
        <p class="text-ink-soft font-600">还没有订阅任何创作者</p>
        <p class="text-xs mt-1.5 max-w-[24rem] leading-relaxed">
          在上方粘贴作者主页链接：抖音为 douyin.com/user/xxx 主页，YouTube
          为频道「视频」页，B站为空间页
        </p>
      </div>
    </div>

    <div v-else>
      <div class="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-4 gap-3">
        <article
          v-for="s in subs"
          :key="s.sid"
          class="card card-hover p-3 flex flex-col gap-2 animate-rise"
        >
          <div class="flex items-start gap-2.5 min-w-0">
            <!-- 头像：小，紧贴名称左侧 -->
            <img
              v-if="s.avatar"
              :src="s.avatar"
              :alt="s.name || s.extractor || '频道'"
              referrerpolicy="no-referrer"
              class="w-9 h-9 shrink-0 rounded-lg object-cover bg-brand-soft"
              @error="$event.target.style.display = 'none'"
            />
            <div
              v-else
              class="w-9 h-9 shrink-0 rounded-lg grid place-items-center text-sm font-700 bg-brand-soft text-brand"
            >
              {{ (s.name || s.extractor || 'R').slice(0, 1) }}
            </div>

            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-1 min-w-0">
                <h3 class="text-sm font-600 text-ink truncate">
                  {{ s.name || s.extractor || '待识别频道' }}
                </h3>
                <span v-if="s.extractor" class="chip !py-0 !px-1.5 text-[10px] shrink-0">
                  {{ s.extractor }}
                </span>
              </div>
              <span v-if="s.unique_id" class="text-[10px] text-ink-faint truncate">
                {{ s.extractor === 'Douyin' ? '抖音号' : 'ID' }}：{{ s.unique_id }}
              </span>
            </div>

            <!-- 右上角：刷新（重探账号）+ 删除 -->
            <div class="flex items-center gap-0.5 shrink-0 -mt-0.5 -mr-1">
              <button
                class="h-7 w-7 grid place-items-center rounded-lg text-ink-faint hover:text-brand hover:bg-brand/5 cursor-pointer transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                :disabled="s.reprobing"
                :title="s.reprobing ? '探测中' : '重新探测账号信息（头像/简介/粉丝等）'"
                @click="onReprobe(s)"
              >
                <span
                  :class="
                    s.reprobing ? 'i-lucide-loader-circle animate-spin' : 'i-lucide-refresh-cw'
                  "
                />
              </button>
              <button
                class="h-7 w-7 grid place-items-center rounded-lg text-ink-faint hover:text-bad hover:bg-bad/5 cursor-pointer transition-colors"
                title="删除订阅（不删已采视频）"
                @click="onRemove(s)"
              >
                <span class="i-lucide-trash-2" />
              </button>
            </div>
          </div>
          <p
            v-if="s.signature"
            class="text-[11px] text-ink-soft mt-0.5 leading-snug line-clamp-2"
            :title="s.signature"
          >
            简介：{{ s.signature }}
          </p>
          <!-- 统计：两行 -->
          <div class="text-[11px] text-ink-faint/70 leading-snug">
            <div>
              <template v-if="s.video_count != null"> 作品 {{ s.video_count }}</template>
              <template v-if="s.follower_count != null">
                · 粉丝 {{ fmtNum(s.follower_count) }}</template
              >
              <template v-if="s.total_favorited != null">
                · 获赞 {{ fmtNum(s.total_favorited) }}</template
              >
              <template v-if="s.mplatform_followers_count != null">
                · 多平台粉 {{ fmtNum(s.mplatform_followers_count) }}</template
              >
            </div>
            <div v-if="s.ip_location || s.country || s.city" class="text-ink-faint">
              <template v-if="s.ip_location">{{ s.ip_location }}</template>
              <template v-if="s.country || s.city">
                · 账号属地：{{ [s.country, s.city].filter(Boolean).join('·') }}</template
              >
            </div>
          </div>

          <!-- 检查状态 -->
          <div class="text-[11px] text-ink-faint/70">
            <template v-if="s.checking"><span class="text-brand">检查中</span></template>
            <template v-else-if="s.last_error"><span class="text-bad">失败</span></template>
            <template v-else-if="s.last_check_at"
              >最近更新：{{ fromNow(s.last_check_at)
              }}<template v-if="s.collected_count != null">
                · 已采 {{ s.collected_count }}条</template
              ></template
            >
          </div>

          <!-- 操作 -->
          <div class="mt-auto flex items-center gap-1">
            <span class="text-[11px] text-ink-faint shrink-0">定时采集</span>
            <select
              class="h-7 px-1 rounded-lg border border-line bg-white text-[10px] text-ink-soft outline-none cursor-pointer hover:border-brand/40 transition-colors w-15 shrink-0"
              :value="s.check_hour ?? ''"
              title="每天自动检查的时间；选「不开启」则只在你手动点采集时才检查"
              @change="onSchedule(s, $event)"
            >
              <option value="">不开启</option>
              <option v-for="h in 24" :key="h - 1" :value="h - 1">
                {{ String(h - 1).padStart(2, '0') }}:00
              </option>
            </select>
            <span class="text-[11px] text-ink-faint shrink-0">条数</span>
            <input
              :value="s.enum_limit"
              type="number"
              min="1"
              max="50"
              class="h-7 w-12 px-1.5 rounded-lg border border-line bg-white text-[10px] text-ink-soft outline-none focus:border-brand transition-colors shrink-0"
              title="每次采集最新几条视频"
              @change="onLimit(s, $event)"
            />
            <button
              class="h-7 px-2 rounded-lg text-[10px] btn-primary shrink-0"
              :disabled="s.checking"
              title="后台枚举最新视频并自动采集（与库中比对，已存在的跳过）"
              @click="onCheck(s)"
            >
              <span
                :class="s.checking ? 'i-lucide-loader-circle animate-spin' : 'i-lucide-rotate-ccw'"
              />{{ s.checking ? '采集中' : '立即采集' }}
            </button>
          </div>
        </article>
      </div>
      <p class="text-xs text-ink-faint/70 text-center pt-4">
        每次采集最新若干条，与库比对后只采新视频，下载排队与配额自动控速；「条数」框设定每次采集几条，「定时采集」框设定每天几点自动检查（不设则只手动采集）
      </p>
    </div>

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
