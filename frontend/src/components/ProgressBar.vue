<script setup>
defineProps({
  percent: { type: Number, default: null } // null → 不确定态
})
</script>

<template>
  <div class="h-1.5 w-full rounded-full bg-surface-2 overflow-hidden">
    <div
      v-if="percent != null"
      class="h-full rounded-full grad-brand transition-[width] duration-500 ease-out"
      :style="{ width: Math.min(100, Math.max(0, percent)) + '%' }"
    />
    <div
      v-else
      class="h-full w-1/3 rounded-full grad-brand"
      style="animation: bar-slide 1.1s ease-in-out infinite"
    />
  </div>
</template>

<style scoped></style>
