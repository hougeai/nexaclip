import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from '@/utils'

const routes = [
  // ---- 公开：营销落地页 + 认证 ----
  { path: '/', name: 'landing', component: () => import('@/views/LandingView.vue') },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/AuthView.vue'),
    props: { mode: 'login' },
    meta: { guestOnly: true }
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/views/AuthView.vue'),
    props: { mode: 'register' },
    meta: { guestOnly: true }
  },
  {
    // 邮件重置链接落地页：/reset-password?token=xxx
    path: '/reset-password',
    name: 'reset-password',
    component: () => import('@/views/ResetPasswordView.vue')
  },

  // ---- 控制台：登录后才可进入，左侧导航 ----
  {
    path: '/app',
    component: () => import('@/layouts/ConsoleLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: { name: 'collect' } },
      {
        path: 'collect',
        name: 'collect',
        component: () => import('@/views/console/DownloadView.vue')
      },
      {
        path: 'history',
        name: 'history',
        component: () => import('@/views/console/HistoryView.vue')
      },
      {
        path: 'kb',
        name: 'kb',
        component: () => import('@/views/console/KnowledgeView.vue')
      },
      {
        path: 'subs',
        name: 'subs',
        component: () => import('@/views/console/SubscriptionsView.vue')
      }
    ]
  },

  { path: '/:pathMatch(.*)*', redirect: '/' }
]

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior: () => ({ top: 0 })
})

router.beforeEach(to => {
  const authed = !!getToken()
  if (to.meta.requiresAuth && !authed) {
    return { name: 'login', query: { next: to.fullPath } }
  }
  // 已登录不必再看登录/注册页
  if (to.meta.guestOnly && authed) {
    return { name: 'collect' }
  }
  return true
})
