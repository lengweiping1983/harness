import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'
import { isAbsolute, relative, resolve, sep } from 'node:path'
import type {IncomingMessage} from 'node:http'
import postcssConfig from './postcss.config.js'

const backendUrl = process.env.HARNESS_BACKEND_URL || 'http://127.0.0.1:8765'
const apiPrefix = '^/api(?:/|\\?|$)'
const expectContinue = new WeakSet<IncomingMessage>()

// 前端与 Python 后端完全解耦：开发期代理到 HARNESS_BACKEND_URL，默认本地 8765，
// 生产期由 harness.web.static_assets 直接托管 dist/。
export default defineConfig({
  plugins: [react(), {
    name: 'harness-api-continue',
    configureServer(server) {
      const http = server.httpServer
      if (!http) return
      http.on('checkContinue', (request, response) => {
        // 浏览器入口也须等后端准入，不能由 Node 先自动确认可上传。
        if (new RegExp(apiPrefix).test(request.url ?? '')) expectContinue.add(request)
        else response.writeContinue()
        http.emit('request', request, response)
      })
    },
  }, {
    name: 'harness-web-watch-boundary',
    config(config) {
      const root = resolve(config.root ?? '.')
      return { server: { watch: { ignored: [(file: string) => {
        const path = resolve(file)
        // 允许遍历根目录的祖先，但插件额外注册的目录外文件不能触发热更新。
        if (path === root || root.startsWith(path.endsWith(sep) ? path : path + sep)) return false
        const fromRoot = relative(root, path)
        return isAbsolute(fromRoot) || fromRoot === '..' || fromRoot.startsWith('..' + sep)
      }] } } }
    },
  }],
  // 将样式配置纳入 Vite 配置依赖，变更时重载，避免常驻进程复用旧的 Tailwind 配置。
  css: { postcss: postcssConfig },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  // 终端首次打开前完成 CommonJS 预构建，避免懒加载时才重新优化依赖。
  optimizeDeps: { include: ['@xterm/xterm', '@xterm/addon-fit'] },
  server: {
    port: 5273,
    // API 的预检与响应头由后端统一决定，开发入口不提前应答或追加许可。
    cors: false,
    // 测试代码不进入开发热更新；产品源码仍由 Vite 正常跟踪。
    watch: {
      ignored: ['**/tests/**', '**/test/**', '**/__tests__/**', '**/*.test.*', '**/*.spec.*', '**/test_*'],
    },
    proxy: {
      // Preserve Host so the backend can compare it with the browser Origin.
      [apiPrefix]: {
        target: backendUrl, changeOrigin: false,
        bypass(incoming) {
          // Vite 已消费的临时回执由代理与后端重新协商，仍须经过正文准入。
          if (incoming.headers.expect?.toLowerCase() === '100-continue') delete incoming.headers.expect
        },
        configure(proxy) {
          // 先转发声明头，让后端在客户端尚未发送正文时也能拒绝超限请求。
          proxy.on('proxyReq', (request, incoming, response) => {
            if (Number(incoming.headers['content-length'] ?? 0) > 0) {
              // 后端确认长度后再上传，避免早期 413 与仍在写入的代理请求竞争。
              incoming.unpipe(request)
              incoming.pause()
              request.setHeader('Expect', '100-continue')
              request.once('continue', () => {
                if (incoming.destroyed || response.destroyed) return
                if (expectContinue.delete(incoming)) response.writeContinue()
                incoming.pipe(request)
              })
            }
            request.flushHeaders()
          })
        },
      },
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
