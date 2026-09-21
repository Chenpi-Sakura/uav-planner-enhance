import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
// 后端端口唯一来源仍是仓库根 config.json；这里用读文件而非 import，因为 tsconfig.node 未开 resolveJsonModule。
const { backend } = JSON.parse(
  readFileSync(new URL('../config.json', import.meta.url), 'utf-8'),
) as { backend: { port: number } }

export default defineConfig({
  plugins: [vue()],
  // 把依赖预构建缓存放到仓库根下可写目录，避免 node_modules 权限受限导致的 EPERM
  cacheDir: '../.vite-cache',
  server: {
    // 开发页面统一请求本站 /api/*，由 Vite 转发到后端，与公网同源部署口径一致
    proxy: {
      '/api': { target: `http://127.0.0.1:${backend.port}` },
    },
    fs: {
      // cacheDir 在 frontend 之外，预构建产物以 /@fs/ 绝对路径回源，需放行仓库根目录
      allow: ['..'],
    },
  },
})
