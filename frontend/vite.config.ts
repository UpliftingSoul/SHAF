/** 开发与测试配置：浏览器始终请求 /api，开发代理连接独立后端。 */
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [vue()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      proxy: { '/api': { target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true } },
    },
    test: { environment: 'jsdom', include: ['tests/unit/**/*.test.ts'], restoreMocks: true },
  }
})
