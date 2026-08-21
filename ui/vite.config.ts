import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Tauri dev 模式固定端口 1420（tauri.conf.json devUrl 指向此处）
export default defineConfig({
  plugins: [vue()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ['**/src-tauri/**'] },
  },
  envPrefix: 'VITE_',
})
