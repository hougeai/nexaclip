<script setup>
const auth = useAuthStore()
const ui = useUiStore()
const router = useRouter()
const route = useRoute()

const nav = [
  { name: 'collect', label: '视频采集', icon: 'i-lucide-clapperboard' },
  { name: 'subs', label: '订阅账号', icon: 'i-lucide-rss' },
  { name: 'history', label: '历史记录', icon: 'i-lucide-history' },
  { name: 'kb', label: '知识库', icon: 'i-lucide-library' }
]

function logout() {
  auth.logout()
  ui.info('已退出登录')
  router.push({ name: 'landing' })
}
</script>

<template>
  <div class="min-h-full flex bg-surface-1">
    <!-- ===== 左侧导航栏 ===== -->
    <aside
      class="w-16 lg:w-60 shrink-0 bg-white border-r border-line flex flex-col sticky top-0 h-screen"
    >
      <!-- 品牌 -->
      <RouterLink
        to="/"
        class="h-16 flex items-center gap-2.5 px-4 lg:px-5 border-b border-line"
        title="返回首页"
      >
        <img src="/favicon.svg" alt="NexaClip" class="w-8 h-8 shrink-0" />
        <span class="hidden lg:inline font-display text-lg font-800 tracking-tight"
          >Nexa<span class="text-grad">Clip</span></span
        >
      </RouterLink>

      <!-- 导航 -->
      <nav class="flex-1 p-2.5 lg:p-3 space-y-1">
        <RouterLink
          v-for="n in nav"
          :key="n.name"
          :to="{ name: n.name }"
          class="nav-item"
          :class="route.name === n.name ? 'nav-active' : 'nav-inactive'"
          :title="n.label"
        >
          <span :class="[n.icon, 'text-lg']" />
          <span class="hidden lg:inline">{{ n.label }}</span>
        </RouterLink>
      </nav>

      <!-- 用户区 -->
      <div class="p-2.5 lg:p-3 border-t border-line">
        <div class="flex items-center gap-3 justify-center lg:justify-start px-1 lg:px-2 py-2">
          <span
            class="w-8 h-8 shrink-0 rounded-full bg-gradient-to-br from-brand to-brand-dark grid place-items-center text-white text-xs font-700"
          >
            {{ auth.displayName.slice(0, 1).toUpperCase() }}
          </span>
          <div class="hidden lg:block min-w-0 flex-1">
            <div class="text-sm font-medium truncate">{{ auth.displayName }}</div>
            <div class="text-xs text-ink-faint truncate">{{ auth.user?.email }}</div>
          </div>
        </div>
        <button
          class="w-full flex items-center justify-center lg:justify-start gap-3 h-10 px-3 lg:px-3.5 rounded-xl text-sm text-ink-soft hover:bg-surface-1 hover:text-bad transition-colors"
          title="退出登录"
          @click="logout"
        >
          <span class="i-lucide-log-out text-lg" />
          <span class="hidden lg:inline">退出登录</span>
        </button>
      </div>
    </aside>

    <!-- ===== 主内容 ===== -->
    <div class="flex-1 min-w-0 flex flex-col">
      <main class="flex-1">
        <RouterView v-slot="{ Component }">
          <Transition name="fade" mode="out-in">
            <component :is="Component" :key="$route.fullPath" />
          </Transition>
        </RouterView>
      </main>
    </div>
  </div>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
