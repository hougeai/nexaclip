import axios from 'axios'
import { API_BASE } from '@/config'
import { getToken, setToken, getRefreshToken, setRefreshToken, clearTokens } from '@/utils'

const service = axios.create({
  baseURL: API_BASE,
  timeout: 60000
})

// 业务错误：带 msg + code，供上层 toast
export class ApiError extends Error {
  constructor(msg, code) {
    super(msg || '请求失败')
    this.code = code
  }
}

// ============ 请求：注入 token 头（无 cookie，全走 header） ============
service.interceptors.request.use(config => {
  if (!config.noAuth) {
    const token = getToken()
    if (token) config.headers.token = token
  }
  return config
})

// ============ 刷新（单飞：并发只发一次，其余等待复用） ============
let refreshing = null
function doRefresh() {
  if (refreshing) return refreshing
  const rt = getRefreshToken()
  if (!rt) return Promise.reject(new ApiError('未登录', 401))
  refreshing = axios
    .post(`${API_BASE}/base/refresh_token`, { refresh_token: rt })
    .then(res => {
      const body = res.data
      if (body.code !== 200) throw new ApiError(body.msg, body.code)
      setToken(body.data.access_token)
      setRefreshToken(body.data.refresh_token)
      return body.data.access_token
    })
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

// ============ 响应：拆 {code,msg,data}；401/419 刷新重试一次 ============
service.interceptors.response.use(
  // 成功：HTTP 2xx，直接返回业务体（保留 total 等分页字段）
  res => {
    const body = res.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 200) return body
      throw new ApiError(body.msg, body.code)
    }
    return body
  },
  // 失败：HTTP 非 2xx
  async error => {
    const { response, config } = error
    if (!response) throw new ApiError('网络异常，请检查连接')

    const status = response.status
    const msg = response.data?.msg || response.data?.detail || '请求失败'

    // access token 过期/无效 → 刷新后重试一次（refresh 接口本身不重试）
    const canRetry = (status === 401 || status === 419) && !config._retried && !config.noAuth
    if (canRetry) {
      config._retried = true
      try {
        const newToken = await doRefresh()
        config.headers.token = newToken
        return service(config)
      } catch {
        clearTokens()
        throw new ApiError('登录已失效，请重新登录', 401)
      }
    }
    throw new ApiError(msg, status)
  }
)

export default service
