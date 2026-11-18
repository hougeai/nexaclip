<script setup>
import api from '@/api'

const props = defineProps({ mode: { type: String, default: 'login' } }) // 'login' | 'register'

const ui = useUiStore()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const view = ref(props.mode) // 'login' | 'register' | 'forgot'
watch(
  () => props.mode,
  v => (view.value = v)
)

const loading = ref(false)
const loginForm = reactive({ email: '', password: '' })
const regForm = reactive({ user_name: '', email: '', password: '', verification_code: '' })
const forgotForm = reactive({ email: '' })

const codeLeft = ref(0)
let timer = null
function startCountdown(sec = 60) {
  codeLeft.value = sec
  timer = setInterval(() => {
    if (--codeLeft.value <= 0) clearInterval(timer)
  }, 1000)
}
onUnmounted(() => timer && clearInterval(timer))

async function sendCode() {
  if (!regForm.email) return ui.error('请先填写邮箱')
  try {
    loading.value = true
    await auth.sendCode(regForm.email)
    ui.success('验证码已发送，请查收邮箱')
    startCountdown()
  } catch (e) {
    ui.error(e.message || '发送失败')
  } finally {
    loading.value = false
  }
}

function done(msg) {
  ui.success(msg)
  const next = route.query.next || { name: 'collect' }
  router.replace(next)
}

async function submitLogin() {
  if (!loginForm.email || !loginForm.password) return ui.error('请填写邮箱和密码')
  try {
    loading.value = true
    await auth.login({ email: loginForm.email, password: loginForm.password })
    done('登录成功')
  } catch (e) {
    ui.error(e.message || '登录失败')
  } finally {
    loading.value = false
  }
}

async function submitRegister() {
  const need = ['user_name', 'email', 'password', 'verification_code']
  if (need.some(k => !regForm[k])) return ui.error('请填写完整注册信息')
  if (regForm.password.length < 6) return ui.error('密码至少 6 位')
  try {
    loading.value = true
    await auth.register({ ...regForm })
    await auth.login({ email: regForm.email, password: regForm.password })
    done('注册成功，已自动登录')
  } catch (e) {
    ui.error(e.message || '注册失败')
  } finally {
    loading.value = false
  }
}

async function submitForgot() {
  if (!forgotForm.email) return ui.error('请填写邮箱')
  try {
    loading.value = true
    await api.forgotPassword({ email: forgotForm.email })
    ui.success('重置链接已发送到邮箱，请查收')
    view.value = 'login'
  } catch (e) {
    ui.error(e.message || '发送失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-full bg-surface-1 grid place-items-center px-5 py-10">
    <div class="w-full max-w-md">
      <!-- 品牌 -->
      <RouterLink to="/" class="flex items-center justify-center gap-2.5 mb-8">
        <img src="/favicon.svg" alt="NexaClip" class="w-9 h-9" />
        <span class="font-display text-xl font-800 tracking-tight"
          >Nexa<span class="text-grad">Clip</span></span
        >
      </RouterLink>

      <div class="card p-8">
        <h1 class="font-display text-2xl font-800 tracking-tight">
          {{ view === 'login' ? '欢迎回来' : view === 'register' ? '创建账号' : '找回密码' }}
        </h1>
        <p class="text-dim mt-1.5 mb-7 text-sm">
          {{
            view === 'login'
              ? '登录后进入控制台开始下载'
              : view === 'register'
                ? '注册即可使用云端下载与历史记录'
                : '输入注册邮箱，我们发送重置链接'
          }}
        </p>

        <!-- 找回密码 -->
        <form v-if="view === 'forgot'" class="space-y-4" @submit.prevent="submitForgot">
          <div>
            <label for="fp-email" class="block text-sm font-medium text-ink mb-1.5">邮箱</label>
            <input
              id="fp-email"
              v-model="forgotForm.email"
              type="email"
              placeholder="you@example.com"
              class="field h-12"
            />
          </div>
          <button type="submit" class="btn-primary w-full" :disabled="loading">
            {{ loading ? '发送中…' : '发送重置链接' }}
          </button>
          <button
            type="button"
            class="w-full text-sm text-dim hover:text-ink transition-colors"
            @click="view = 'login'"
          >
            返回登录
          </button>
        </form>

        <!-- 登录 -->
        <form v-else-if="view === 'login'" class="space-y-4" @submit.prevent="submitLogin">
          <div>
            <label for="li-email" class="block text-sm font-medium text-ink mb-1.5">邮箱</label>
            <input
              id="li-email"
              v-model="loginForm.email"
              type="email"
              autocomplete="username"
              placeholder="you@example.com"
              class="field h-12"
            />
          </div>
          <div>
            <div class="flex items-center justify-between mb-1.5">
              <label for="li-pass" class="text-sm font-medium text-ink">密码</label>
              <button
                type="button"
                class="text-sm text-brand hover:text-brand-dark transition-colors"
                @click="view = 'forgot'"
              >
                忘记密码？
              </button>
            </div>
            <input
              id="li-pass"
              v-model="loginForm.password"
              type="password"
              autocomplete="current-password"
              placeholder="••••••••"
              class="field h-12"
            />
          </div>
          <button type="submit" class="btn-primary w-full mt-2" :disabled="loading">
            {{ loading ? '登录中…' : '登录' }}
          </button>
          <p class="text-center text-sm text-dim pt-1">
            还没有账号？
            <RouterLink
              :to="{ name: 'register' }"
              class="text-brand font-medium hover:text-brand-dark transition-colors"
              >免费注册</RouterLink
            >
          </p>
        </form>

        <!-- 注册 -->
        <form v-else class="space-y-4" @submit.prevent="submitRegister">
          <div>
            <label for="rg-name" class="block text-sm font-medium text-ink mb-1.5">用户名</label>
            <input
              id="rg-name"
              v-model="regForm.user_name"
              placeholder="怎么称呼你"
              class="field h-12"
            />
          </div>
          <div>
            <label for="rg-email" class="block text-sm font-medium text-ink mb-1.5">邮箱</label>
            <input
              id="rg-email"
              v-model="regForm.email"
              type="email"
              autocomplete="username"
              placeholder="you@example.com"
              class="field h-12"
            />
          </div>
          <div>
            <label for="rg-code" class="block text-sm font-medium text-ink mb-1.5">验证码</label>
            <div class="flex gap-2">
              <input
                id="rg-code"
                v-model="regForm.verification_code"
                inputmode="numeric"
                placeholder="6 位数字"
                class="field h-12 flex-1"
              />
              <button
                type="button"
                class="btn-ghost !px-4 whitespace-nowrap shrink-0"
                :disabled="codeLeft > 0 || loading"
                @click="sendCode"
              >
                {{ codeLeft > 0 ? `${codeLeft}s` : '发送验证码' }}
              </button>
            </div>
          </div>
          <div>
            <label for="rg-pass" class="block text-sm font-medium text-ink mb-1.5">设置密码</label>
            <input
              id="rg-pass"
              v-model="regForm.password"
              type="password"
              autocomplete="new-password"
              placeholder="至少 6 位"
              class="field h-12"
            />
          </div>
          <button type="submit" class="btn-primary w-full mt-2" :disabled="loading">
            {{ loading ? '注册中…' : '注册并登录' }}
          </button>
          <p class="text-center text-sm text-dim pt-1">
            已有账号？
            <RouterLink
              :to="{ name: 'login' }"
              class="text-brand font-medium hover:text-brand-dark transition-colors"
              >去登录</RouterLink
            >
          </p>
        </form>
      </div>

      <p class="text-center text-xs text-ink-faint mt-6">
        继续即表示同意
        <RouterLink to="/" class="underline hover:text-ink">服务条款</RouterLink> 与隐私政策
      </p>
    </div>
  </div>
</template>
