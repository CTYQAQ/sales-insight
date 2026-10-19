import axios, { AxiosError } from 'axios'
import type { AxiosRequestConfig } from 'axios'

const BASE = '/api'

/** access token 只存内存：刷新页面即失效，靠 refresh(httpOnly Cookie) 静默续期。
 *  放 localStorage 会被 XSS 一锅端，这里刻意不落盘。 */
let accessToken: string | null = null

export function setAccessToken(t: string | null) {
  accessToken = t
}
export function getAccessToken() {
  return accessToken
}

const raw = axios.create({
  baseURL: BASE,
  timeout: 120_000,
  headers: { 'Content-Type': 'application/json' },
})

/** 对外暴露的客户端统一剥掉 AxiosResponse 外壳，直接返回业务数据。
 *  好处：所有 api.* 调用点拿到的是干净类型，不用到处写 .res.data。 */
export const http = {
  get: <T>(url: string, config?: AxiosRequestConfig) => raw.get<T>(url, config).then((r) => r.data),
  post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    raw.post<T>(url, data, config).then((r) => r.data),
  patch: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    raw.patch<T>(url, data, config).then((r) => r.data),
  delete: <T>(url: string, config?: AxiosRequestConfig) => raw.delete<T>(url, config).then((r) => r.data),
}

raw.interceptors.request.use((cfg) => {
  if (accessToken) cfg.headers.Authorization = `Bearer ${accessToken}`
  return cfg
})

let refreshing: Promise<string> | null = null

/** access token 失效时由 AuthContext 监听，用来把内存里的登录态清干净。
 *  派事件而不是直接改 location.href —— 硬跳整页刷新，会把 SPA 的路由狀態也一起冲掉。 */
export const SESSION_EXPIRED_EVENT = 'auth:session-expired'

/** 统一的「拿 httpOnly 的 refresh cookie 换一张 access token」入口。
 *
 *  并发调用共享同一个 in-flight Promise，这点很关键：
 *  - 页面刚加载时多个组件可能同时 401
 *  - React StrictMode 会把 useEffect 跑两遍
 *  如果各换各的，refresh token 是「轮换式」的 —— 第二次拿到的 cookie 已被第一次作废，
 *  直接 401，反而把人踢下线。所以必须去重，一次只认正在飞的那一个请求。
 */
export function refreshAccessToken(): Promise<string> {
  if (!refreshing) {
    refreshing = raw
      .post<{ access_token: string }>('/auth/refresh')
      .then((r) => {
        setAccessToken(r.data.access_token)
        return r.data.access_token
      })
      .catch((e: unknown) => {
        setAccessToken(null)
        window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT))
        throw e
      })
      .finally(() => {
        refreshing = null
      })
  }
  return refreshing
}

raw.interceptors.response.use(
  (r) => r,
  async (error: AxiosError) => {
    const status = error.response?.status
    const url = error.config?.url ?? ''

    // 401 且不是刷新接口本身 -> 用 refresh 换一个 access 再重放原请求。
    // 这里要求手上原本有 token：连 token 都没有说明压根没登录过或正处于启动恢复阶段，
    // 重试只会制造无意义的请求。
    if (status === 401 && accessToken && !url.includes('/auth/refresh')) {
      try {
        const token = await refreshAccessToken()
        if (error.config) {
          error.config.headers.Authorization = `Bearer ${token}`
          return raw.request(error.config)
        }
      } catch {
        // refreshAccessToken 已派发失效事件并清掉 token，这里只需让请求失败
      }
    }
    return Promise.reject(error)
  },
)

/** 把后端统一错误结构翻译成能直接给用户看的中文文案 */
export function errMsg(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const data = e.response?.data as { message?: string; code?: string } | undefined
    if (data?.message) return data.message

    // 没有业务响应体的 500，最常见的原因不是"后端抛异常"，而是**后端根本没启动**。
    //
    // 这是因为请求先到 vite 的 dev 代理：后端不在时代理拿不到 upstream，
    // 会自己吐一个空的 500（Content-Type: text/plain），axios 于是抛出
    // "Request failed with status code 500" —— 看起来像服务端 bug，
    // 实际只是忘了开后端。所以这里要先探一下健康检查再下结论。
    if (e.response && e.response.status >= 500 && !data?.code) {
      return '服务异常。若刚启动前端，请确认后端已在 8000 端口运行（cd backend && python run.py）'
    }

    if (e.response?.status === 401) return '登录已失效，请重新登录'
    if (e.response?.status === 403) return '没有该操作的权限'
    if (e.response?.status === 404) return '资源不存在'
    if (e.response?.status === 422) return '提交的数据有误，请检查'
    if (e.response && e.response.status >= 500) return '服务异常，请稍后重试'
    if (!e.response) return '网络连接失败，请检查后端是否已启动'
    return `请求失败（${e.response.status}）`
  }
  // 非 axios 错误（含普通 Error）才用 message
  if (e instanceof Error && e.message) return e.message
  return String(e)
}

export interface ApiResult<T> {
  data: T
}
