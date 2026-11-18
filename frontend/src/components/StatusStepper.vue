<script setup>
// 采集状态节点：采集 → 下载 → 文案 → 报告。下载页卡片与历史卡片共用。
// 「下载」节点变绿即代表成品就绪（status=ready），不再单列「就绪」节点。
const props = defineProps({
  status: { type: String, required: true }, // video.status
  hasTranscript: { type: Boolean, default: false },
  transcriptFailed: { type: Boolean, default: false },
  aiStatus: { type: String, default: 'none' } // none/running/done/failed
})

const ready = computed(() => props.status === 'ready')

// state: done=绿勾 / active=转圈 / pending=灰 / failed=红叉
const AI_STATE = { none: 'pending', running: 'active', done: 'done', failed: 'failed' }

const steps = computed(() => {
  const s = props.status
  const t = props.hasTranscript
    ? 'done'
    : props.transcriptFailed
      ? 'failed'
      : ready.value
        ? 'active'
        : 'pending'
  return [
    { key: 'collect', label: '采集', state: 'done' },
    {
      key: 'download',
      label: '下载',
      state: s === 'failed' ? 'failed' : s === 'downloading' || s === 'queued' ? 'active' : 'done'
    },
    { key: 'transcript', label: '文案', state: t },
    { key: 'ai', label: '报告', state: AI_STATE[props.aiStatus] || 'pending' }
  ]
})

const STEP_CLS = {
  done: 'bg-ok-soft text-ok',
  active: 'bg-brand-soft text-brand',
  failed: 'bg-bad-soft text-bad',
  pending: 'bg-surface-2 text-ink-faint/70'
}
const STEP_ICON = {
  done: 'i-lucide-check',
  active: 'i-lucide-loader-circle animate-spin',
  failed: 'i-lucide-x',
  pending: 'i-lucide-circle'
}
const stepCls = st => STEP_CLS[st] || STEP_CLS.pending
const stepIcon = st => STEP_ICON[st] || STEP_ICON.pending
</script>

<template>
  <div class="flex items-start pt-1">
    <template v-for="(st, i) in steps" :key="st.key">
      <div
        v-if="i > 0"
        class="flex-1 h-0.5 mt-3 rounded-full transition-colors"
        :class="st.state === 'pending' ? 'bg-line' : 'bg-ok/50'"
      />
      <div class="flex flex-col items-center gap-1 w-11 shrink-0">
        <span class="w-6 h-6 rounded-full grid place-items-center" :class="stepCls(st.state)">
          <span :class="stepIcon(st.state)" />
        </span>
        <span
          class="text-[10px] leading-none"
          :class="st.state === 'pending' ? 'text-ink-faint/70' : 'text-ink-soft'"
        >
          {{ st.label }}
        </span>
      </div>
    </template>
  </div>
</template>

<style scoped></style>
