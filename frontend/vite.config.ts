import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  // 把依赖预构建缓存放到仓库根下可写目录，避免 node_modules 权限受限导致的 EPERM
  cacheDir: '../.vite-cache',
  server: {
    fs: {
      // 允许从仓库根目录导入 config.json（统一端口配置）
      allow: ['..'],
    },
  },
})
