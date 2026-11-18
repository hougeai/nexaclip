<script setup>
import { parseUrls } from '@/utils'

const props = defineProps({ loading: { type: Boolean, default: false } })
const emit = defineEmits(['fetch'])

const text = defineModel('text', { type: String, default: '' })

const count = computed(() => parseUrls(text.value).length)

async function paste() {
  try {
    const clip = await navigator.clipboard.readText()
    if (clip) text.value = (text.value ? text.value + '\n' : '') + clip.trim()
  } catch {
    /* 剪贴板权限被拒，忽略 */
  }
}

function submit() {
  if (!count.value || props.loading) return
  emit('fetch', text.value)
}
</script>

<template>
  <div class="card p-4 sm:p-5">
    <div class="relative">
      <label for="url-input" class="sr-only">视频链接</label>
      <textarea
        id="url-input"
        v-model="text"
        rows="3"
        placeholder="粘贴一个或多个视频链接，每行一个&#10;支持 YouTube / Bilibili / 抖音 / TikTok 等 1000+ 站点"
        class="field resize-none py-3.5 leading-relaxed pr-24 text-[15px]"
        @keydown.meta.enter.prevent="submit"
        @keydown.ctrl.enter.prevent="submit"
      />
      <button class="btn-ghost-sm absolute top-3 right-3" title="从剪贴板粘贴" @click="paste">
        <span class="i-lucide-clipboard" />粘贴
      </button>
    </div>

    <div class="mt-4 flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
      <span class="hidden sm:block flex-1" />

      <span v-if="count > 1" class="chip !text-brand !bg-brand-soft !border-brand/30 sm:mr-1">
        <span class="i-lucide-link-2" />已识别 {{ count }} 个链接
      </span>

      <button class="btn-primary shrink-0" :disabled="!count || loading" @click="submit">
        <span :class="loading ? 'i-lucide-loader-circle animate-spin' : 'i-lucide-sparkles'" />
        {{ loading ? '解析中…' : count ? `获取 ${count} 个` : '获取' }}
      </button>
    </div>

    <p class="mt-3 text-xs text-ink-faint hidden sm:flex items-center gap-1.5">
      <span>提示：</span>
      <kbd class="kbd">⌘/Ctrl + Enter</kbd>
      <span>快速解析</span>
    </p>
  </div>
</template>

<style scoped></style>
