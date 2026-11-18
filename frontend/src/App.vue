<script setup>
import api from '@/api'

const auth = useAuthStore()

onMounted(async () => {
  auth.ensureUser()
  // 冷启动预热：HF Spaces 免费档休眠时，这一发帮后端唤醒（失败无妨）
  try {
    await api.health()
  } catch {
    /* ignore */
  }
})
</script>

<template>
  <div class="min-h-full">
    <RouterView v-slot="{ Component }">
      <Transition name="fade" mode="out-in">
        <component :is="Component" />
      </Transition>
    </RouterView>
    <Toaster />
  </div>
</template>

<style>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.25s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
