import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// 所有 /api 与 /health /ready 请求代理到后端，前端只认相对路径，
// 换部署地址时不用改任何业务代码，也顺带规避了 cookie 跨域问题。
//
// 后端端口用 VITE_API_TARGET 覆盖，方便本机同时跑多个后端实例做对比。
// 用 loadEnv 读环境变量：tsconfig 只 include src，拿不到 @types/node，
// 直接写 process.env 在编辑器里会报红。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const apiTarget = env.VITE_API_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      proxy: {
        '/api': { target: apiTarget, changeOrigin: true },
        '/health': { target: apiTarget, changeOrigin: true },
        '/ready': { target: apiTarget, changeOrigin: true },
      },
    },
  }
})
