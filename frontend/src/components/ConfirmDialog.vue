<script setup>
defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '确认操作' },
  message: { type: String, default: '' },
  confirmText: { type: String, default: '确定' },
  cancelText: { type: String, default: '取消' },
  danger: { type: Boolean, default: false }
})
const emit = defineEmits(['confirm', 'cancel'])

function onConfirm() {
  emit('confirm')
}
function onCancel() {
  emit('cancel')
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-modal grid place-items-center p-4 bg-black/45 backdrop-blur-sm"
    @click.self="onCancel"
  >
    <div class="card w-full max-w-sm p-5 animate-rise">
      <h3 class="text-sm font-600 text-ink mb-1.5">{{ title }}</h3>
      <p class="text-xs text-ink-soft leading-relaxed whitespace-pre-wrap">{{ message }}</p>
      <div class="flex justify-end gap-2 mt-5">
        <button class="btn-ghost !h-9 !px-4 text-sm shrink-0" @click="onCancel">
          {{ cancelText }}
        </button>
        <button v-if="danger" class="btn-danger shrink-0 !h-9 !px-4 !text-sm" @click="onConfirm">
          {{ confirmText }}
        </button>
        <button v-else class="btn-primary shrink-0" @click="onConfirm">
          {{ confirmText }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped></style>
