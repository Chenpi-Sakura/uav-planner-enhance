import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    fs: {
      // 允许从仓库根目录导入 config.json（统一端口配置）
      allow: ['..'],
    },
  },
})
