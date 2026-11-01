import { defineConfig } from 'vitest/config'
import { fileURLToPath, URL } from 'node:url'

/** 与 vite.config.ts 共用 `@/` 别名，否则测试里 import '@/lib/diff' 解析不了。 */
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    // 限制工作进程争用，避免模块首次转换挤占业务用例的超时时间。
    maxWorkers: 2,
    minWorkers: 1,
    environment: 'node',
    setupFiles: ['./tests/unit-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
