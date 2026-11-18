import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/zh-cn'
import { API_BASE } from '@/config'

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

// ============ token 存取（localStorage，无 cookie） ============
const ACCESS_KEY = 'nexaclip_access'
const REFRESH_KEY = 'nexaclip_refresh'

export const getToken = () => localStorage.getItem(ACCESS_KEY) || ''
export const setToken = t => localStorage.setItem(ACCESS_KEY, t)
export const getRefreshToken = () => localStorage.getItem(REFRESH_KEY) || ''
export const setRefreshToken = t => localStorage.setItem(REFRESH_KEY, t)
export const clearTokens = () => {
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

// ============ 格式化 ============
export function formatDuration(sec) {
  if (sec == null) return ''
  const s = Math.floor(sec)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = s % 60
  const pad = n => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(ss)}` : `${m}:${pad(ss)}`
}

export function formatBytes(bytes) {
  if (bytes == null) return ''
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

export function fromNow(t) {
  return t ? dayjs(t).fromNow() : ''
}

// ============ URL 解析：粘贴框 → 去重后的链接数组 ============
export function parseUrls(text) {
  if (!text) return []
  const seen = new Set()
  return text
    .split(/[\s,，;；]+/)
    .map(s => s.trim())
    .filter(Boolean)
    .filter(u => /^https?:\/\//i.test(u))
    .filter(u => (seen.has(u) ? false : (seen.add(u), true)))
}

// ============ 下载链接：后端返回相对路径 /api/v1/media/{vid}/file，<a> 带不了 header，token 拼进 query ============
// 关键：相对路径必须解析到后端 API_BASE，不能落在前端 Pages 域名（SPA 回退会把 /api/* 也返回 index.html，
// 导致"视频"变成 984B 的 HTML，播放器播不了、下载按钮给的是首页源码）。
export function downloadHref(url) {
  if (!url) return ''
  const apiBase = API_BASE.replace(/\/$/, '')
  if (url.startsWith('/api/v1/')) {
    url = apiBase + url.slice(7) // 去掉 /api/v1 前缀，拼 API_BASE
  } else if (url.startsWith('/')) {
    url = apiBase + url
  }
  return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(getToken())
}

// ============ 采集状态元信息（label + 徽标配色 + 是否进行中，全局唯一真相源） ============
const STATUS_META = {
  queued: { label: '下载中', cls: 'text-brand bg-brand-soft', active: true },
  downloading: { label: '视频下载中', cls: 'text-brand bg-brand-soft', active: true },
  ready: { label: '下载完成', cls: 'text-ok bg-ok/10', active: false },
  failed: { label: '失败', cls: 'text-bad bg-bad/10', active: false }
}

export function statusMeta(status) {
  return STATUS_META[status] || { label: status, cls: 'text-ink-faint bg-surface-2', active: false }
}
