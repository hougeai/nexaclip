<script setup>
import api from '@/api'

const ui = useUiStore()
const router = useRouter()
const route = useRoute()

const token = computed(() => route.query.token || '')
const password = ref('')
const confirm = ref('')
const loading = ref(false)

async function submit() {
  if (!token.value) return ui.error('重置链接无效，请重新申请')
  if (password.value.length < 6) return ui.error('密码至少 6 位')
  if (password.value !== confirm.value) return ui.error('两次输入的密码不一致')
  try {
    loading.value = true
    await api.resetPassword({ token: token.value, new_password: password.value })
    ui.success('密码已重置，请重新登录')
    router.replace({ name: 'login' })
  } catch (e) {
    ui.error(e.message || '重置失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-full bg-surface-1 grid place-items-center px-5 py-10">
    <div class="w-full max-w-md">
      <div class="flex items-center justify-center gap-2.5 mb-8">
        <img src="/favicon.svg" alt="NexaClip" class="w-9 h-9" />
        <span class="font-display text-xl font-800 tracking-tight"
          >Nexa<span class="text-grad">Clip</span></span
        >
      </div>

      <div class="card p-8">
        <div class="w-11 h-11 rounded-xl bg-brand-soft grid place-items-center mb-5">
          <span class="i-lucide-key-round text-brand text-xl" />
        </div>
        <h1 class="font-display text-2xl font-800 tracking-tight">设置新密码</h1>
        <p class="text-sm text-dim mt-1.5 mb-7">
          {{ token ? '为你的账号设置一个新密码' : '重置链接无效，请回到登录页重新申请' }}
        </p>

        <form class="space-y-4" @submit.prevent="submit">
          <div>
            <label for="rp-pass" class="block text-sm font-medium text-ink mb-1.5">新密码</label>
            <input
              id="rp-pass"
              v-model="password"
              type="password"
              placeholder="至少 6 位"
              class="field h-12"
              autocomplete="new-password"
            />
          </div>
          <div>
            <label for="rp-confirm" class="block text-sm font-medium text-ink mb-1.5"
              >确认新密码</label
            >
            <input
              id="rp-confirm"
              v-model="confirm"
              type="password"
              placeholder="再次输入"
              class="field h-12"
              autocomplete="new-password"
              @keyup.enter="submit"
            />
          </div>
          <button type="submit" class="btn-primary w-full mt-2" :disabled="loading || !token">
            {{ loading ? '提交中…' : '重置密码' }}
          </button>
        </form>
      </div>
    </div>
  </div>
</template>
