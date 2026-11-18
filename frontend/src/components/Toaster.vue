<script setup>
const ui = useUiStore()

const meta = {
  success: { icon: 'i-lucide-circle-check', cls: 'text-ok' },
  error: { icon: 'i-lucide-circle-alert', cls: 'text-bad' },
  info: { icon: 'i-lucide-info', cls: 'text-brand' }
}
</script>

<template>
  <div
    class="fixed top-4 left-1/2 -translate-x-1/2 z-toast flex flex-col items-center gap-2 w-[92vw] max-w-sm"
  >
    <TransitionGroup name="toast">
      <div
        v-for="t in ui.toasts"
        :key="t.id"
        class="card w-full px-4 py-3 flex items-start gap-3 cursor-pointer"
        @click="ui.dismiss(t.id)"
      >
        <span :class="[meta[t.type].icon, meta[t.type].cls, 'text-lg shrink-0 mt-0.5']" />
        <span class="text-sm text-ink leading-relaxed break-words">{{ t.msg }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: all 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}
.toast-enter-from {
  opacity: 0;
  transform: translateY(-12px) scale(0.96);
}
.toast-leave-to {
  opacity: 0;
  transform: translateY(-8px) scale(0.98);
}
.toast-move {
  transition: transform 0.28s ease;
}
</style>
