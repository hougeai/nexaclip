<script setup>
import { formatDuration, formatBytes, downloadHref } from '@/utils'

const props = defineProps({
  item: { type: Object, required: true } // 采集记录：探测结果 + 下载状态（按 vid 轮询更新）
})
const emit = defineEmits(['retry', 'remove', 'retranscribe'])

const status = computed(() => props.item.status)
const ready = computed(() => status.value === 'ready')
const failed = computed(() => status.value === 'failed')

// 秒 → mm:ss（文案分段时间轴）
const fmtTs = s => {
  const t = Math.floor(s || 0)
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`
}
</script>

<template>
  <article class="card card-hover overflow-hidden flex flex-col animate-rise">
    <!-- 封面：点击跳原视频 -->
    <a
      :href="item.url"
      target="_blank"
      rel="noopener noreferrer"
      class="group/cover relative block aspect-video shrink-0 overflow-hidden bg-surface-2"
      :title="item.url"
    >
      <img
        v-if="item.thumbnail && item.ok"
        :src="item.thumbnail"
        :alt="item.title || '视频封面'"
        referrerpolicy="no-referrer"
        class="w-full h-full object-cover transition-transform duration-300 group-hover/cover:scale-105"
        loading="lazy"
      />
      <div v-else class="w-full h-full grid place-items-center text-ink-faint/30">
        <span class="i-lucide-image-off text-4xl" />
      </div>

      <span
        v-if="item.duration"
        class="overlay-dark num absolute bottom-2 right-2 px-1.5 py-0.5 rounded-md text-xs"
      >
        {{ formatDuration(item.duration) }}
      </span>
      <span
        v-if="item.extractor"
        class="absolute top-2 left-2 text-xs px-2 py-0.5 rounded-full bg-white/90 text-ink-soft font-medium shadow-sm"
      >
        {{ item.extractor }}
      </span>
      <!-- hover 提示：可跳原视频 -->
      <span
        v-if="item.ok"
        class="overlay-dark-sm absolute bottom-2 left-2 w-7 h-7 rounded-full grid place-items-center opacity-0 group-hover/cover:opacity-100 transition-opacity"
      >
        <span class="i-lucide-external-link text-sm" />
      </span>
    </a>

    <!-- 正文 -->
    <div class="p-3.5 flex flex-col gap-2.5 flex-1">
      <div>
        <h3 class="text-sm font-medium leading-snug line-clamp-2 text-ink min-h-[2.4em]">
          {{ item.title || item.url }}
        </h3>
        <p v-if="item.uploader" class="text-xs text-ink-faint mt-1 truncate">{{ item.uploader }}</p>
      </div>

      <!-- 探测失败（未落库） -->
      <div
        v-if="!item.ok"
        class="mt-auto flex items-start gap-2 text-sm text-bad bg-bad-soft border border-bad/20 rounded-lg px-3 py-2.5"
      >
        <span class="i-lucide-triangle-alert shrink-0 mt-0.5" />
        <span class="break-words">{{ item.error || '采集失败' }}</span>
      </div>

      <template v-else>
        <!-- 状态节点 -->
        <StatusStepper
          :status="item.status"
          :has-transcript="!!item.transcript"
          :transcript-failed="!!item.transcript_error"
          :ai-status="item.ai_status"
        />

        <!-- 下载中（含排队 queued）：进度条 -->
        <div v-if="status === 'downloading' || status === 'queued'" class="space-y-2">
          <div class="flex items-center justify-between text-xs">
            <span class="text-brand font-medium">视频下载中</span>
            <span class="num text-ink-faint">
              <template v-if="item.percent != null">{{ item.percent.toFixed(0) }}%</template>
              <template v-if="item.speed"> · {{ item.speed }}</template>
            </span>
          </div>
          <ProgressBar :percent="item.percent" />
        </div>

        <!-- 下载完成：文案 + 保存 -->
        <div v-else-if="ready" class="space-y-2">
          <!-- 文案转写三态 -->
          <details v-if="item.transcript" class="group">
            <summary
              class="text-xs text-brand cursor-pointer select-none flex items-center gap-1.5 list-none"
            >
              <span class="i-lucide-file-text" />
              查看文案（{{ item.transcript.length }} 字）
              <span class="i-lucide-chevron-down transition-transform group-open:rotate-180" />
            </summary>
            <div
              class="mt-2 text-xs leading-relaxed text-ink-soft max-h-40 overflow-y-auto bg-surface-1 rounded-lg p-2.5 space-y-1.5"
            >
              <p v-for="(seg, i) in item.transcript_segments" :key="i" class="flex gap-2">
                <span class="num shrink-0 text-ink-faint tabular-nums">{{ fmtTs(seg.start) }}</span>
                <span>{{ seg.text }}</span>
              </p>
              <p v-if="!item.transcript_segments?.length" class="whitespace-pre-wrap">
                {{ item.transcript }}
              </p>
            </div>
          </details>
          <div
            v-else-if="item.transcript_error"
            class="text-xs text-bad/90 flex items-start gap-1.5"
          >
            <span class="i-lucide-file-warning shrink-0 mt-0.5" />
            <span class="break-words flex-1">文案提取失败：{{ item.transcript_error }}</span>
            <button
              class="shrink-0 text-brand cursor-pointer hover:underline flex items-center gap-0.5"
              title="基于本地成品重新转写"
              @click="emit('retranscribe', item.vid)"
            >
              <span class="i-lucide-rotate-ccw" />重新转写
            </button>
          </div>
          <p v-else class="text-xs text-ink-faint flex items-center gap-1.5">
            <span class="i-lucide-loader-circle animate-spin" />文案提取中…
          </p>

          <div class="flex items-center justify-between text-xs text-ink-faint">
            <span class="num truncate max-w-[55%]" :title="item.filename">{{ item.filename }}</span>
            <span class="num">{{ formatBytes(item.size) }}</span>
          </div>
          <a
            :href="downloadHref(item.download_url)"
            class="btn-cta w-full no-underline"
            :download="item.filename || ''"
          >
            <span class="i-lucide-download" />保存到本地
          </a>
        </div>

        <!-- 失败：可重试 -->
        <div v-else-if="failed" class="space-y-3">
          <p class="text-xs text-bad/90 flex items-start gap-1.5">
            <span class="i-lucide-circle-x shrink-0 mt-0.5" />
            <span class="break-words">{{ item.error || '下载失败，可重试' }}</span>
          </p>
          <div class="flex gap-2">
            <button class="btn-primary flex-1" @click="emit('retry', item.url)">
              <span class="i-lucide-rotate-ccw" />重试
            </button>
            <button
              class="btn-ghost !w-10 !h-10 shrink-0 grid place-items-center hover:!text-bad"
              title="删除"
              @click="emit('remove', item.vid)"
            >
              <span class="i-lucide-trash-2" />
            </button>
          </div>
        </div>
      </template>
    </div>
  </article>
</template>

<style scoped></style>
