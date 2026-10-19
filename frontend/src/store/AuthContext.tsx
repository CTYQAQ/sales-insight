import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { authApi, type UserInfo } from '../api/api'
import { SESSION_EXPIRED_EVENT, refreshAccessToken, setAccessToken } from '../api/client'

interface AuthCtx {
  user: UserInfo | null
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const Ctx = createContext<AuthCtx | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserInfo | null>(null)
  const [loading, setLoading] = useState(true)

  // access token 刻意只放内存（不放 localStorage，避免 XSS 一把梭），所以整页刷新会丢。
  // 但会话不该跟着丢 —— 这里启动时用 httpOnly 的 refresh cookie 静默换一张新 token：
  //   cookie 有效 -> 无感恢复，用户察觉不到自己已经"重登"过
  //   cookie 没有或过期 -> 保持未登录，落到 Login 页
  // loading 期间 App 会挡一层 loading，不让业务组件抢在 token 就绪前发请求。
  useEffect(() => {
    let cancelled = false

    ;(async () => {
      try {
        await refreshAccessToken()
        if (cancelled) return
        const me = await authApi.me()
        if (!cancelled) setUser(me)
      } catch {
        if (!cancelled) setUser(null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    const onExpired = () => {
      setAccessToken(null)
      setUser(null)
    }
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired)
    return () => {
      cancelled = true
      window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired)
    }
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const resp = await authApi.login(username, password)
    setAccessToken(resp.access_token)
    setUser(resp.user)
  }, [])

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } finally {
      setAccessToken(null)
      setUser(null)
    }
  }, [])

  const value = useMemo(() => ({ user, loading, login, logout }), [user, loading, login, logout])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useAuth 必须在 AuthProvider 内使用')
  return ctx
}
