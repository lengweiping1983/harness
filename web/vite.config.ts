import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'
import postcssConfig from './postcss.config.js'

const backendUrl = process.env.HARNESS_BACKEND_URL || 'http://127.0.0.1:8765'

// 前端与 Python 后端完全解耦：开发期代理到 HARNESS_BACKEND_URL，默认本地 8765，
// 生产期由 harness.web.static_assets 直接托管 dist/。
export default defineConfig({
  plugins: [react()],
  // 将样式配置纳入 Vite 配置依赖，变更时重载，避免常驻进程复用旧的 Tailwind 配置。
  css: { postcss: postcssConfig },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  // 终端首次打开前完成 CommonJS 预构建，避免懒加载时才重新优化依赖。
  optimizeDeps: { include: ['@xterm/xterm', '@xterm/addon-fit'] },
  server: {
    port: 5273,
    // 测试代码不进入开发热更新；产品源码仍由 Vite 正常跟踪。
    watch: {
      ignored: ['**/tests/**', '**/test/**', '**/__tests__/**', '**/*.test.*', '**/*.spec.*', '**/test_*'],
    },
    proxy: {
      // Preserve Host so the backend can compare it with the browser Origin.
      '/api/v1': { target: backendUrl, changeOrigin: false },
      '/ws': { target: backendUrl, ws: true, changeOrigin: false },
    },
  },
  build: {
    outDir: 'dist',
    target: 'es2022',
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      input: {app:fileURLToPath(new URL('./index.html', import.meta.url)), voice:fileURLToPath(new URL('./voice.html', import.meta.url))},
      // 这里刻意「少」做手动分包。
      //
      // mermaid 会和 unified/micromark/mdast 这一套共享大量依赖，早先按
      // 关键字把它们分别塞进 viz / markdown 两个 chunk，结果是 viz <-> markdown
      // 成环：构建只报 warning，运行时却会在 mermaid Theme 构造时抛
      // "Cannot access 'x' before initialization"（ESM TDZ）。
      // 改成丢给 vendor 兜底后变成 vendor <-> viz，同样炸。
      //
      // Rollup 的自动切分本身保证无环（共享模块会被提升到公共 chunk），
      // 而 mermaid / katex / highlight.js 都已经是动态 import，
      // 首屏并不会加载它们。所以这里只固定 react 三件套，其余交给 Rollup。
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('react-dom') || id.includes('/react/') || id.includes('scheduler')) {
            return 'react'
          }
          return undefined
        },
      },
    },
  },
})
