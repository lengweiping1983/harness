import { afterEach, beforeAll, expect, it, vi } from 'vitest'
import { createServer, loadConfigFromFile, type UserConfig, type ViteDevServer } from 'vite'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

let server: ViteDevServer | undefined
let root: string | undefined
let config: UserConfig

beforeAll(async () => {
  // 正式配置的打包加载属于夹具准备，不占用文件变化断言的超时预算。
  const loaded = await loadConfigFromFile({ command: 'serve', mode: 'development' }, fileURLToPath(new URL('../../../vite.config.ts', import.meta.url)))
  if (!loaded) throw new Error('无法加载开发配置')
  config = loaded.config
})

afterEach(async () => {
  await server?.close()
  if (root) await rm(root, { recursive: true, force: true })
  server = undefined
  root = undefined
})

// Windows 继续沿正式单测入口使用轮询，其他平台同时核对默认监听和轮询。
it.each(process.platform === 'win32' ? [true] : [undefined, true])('tracks application changes while ignoring test directories and test modules (polling: %s)', async usePolling => {
  root = await mkdtemp(join(tmpdir(), 'harness-dev-watch-'))
  const web = join(root, 'web')
  const src = join(web, 'src')
  const tests = join(web, 'tests')
  const nested = join(src, '__tests__')
  const desktop = join(root, 'desktop')
  const publicDir = join(web, 'public')
  await Promise.all([mkdir(nested, { recursive: true }), mkdir(tests, { recursive: true }), mkdir(desktop), mkdir(publicDir, { recursive: true })])
  const application = join(src, 'main.ts')
  const webFiles = [application, join(src, 'style.css'), join(web, 'index.html'), join(publicDir, 'icon.svg')]
  const outside = [join(desktop, 'package.json'), join(desktop, 'package-lock.json'), join(root, 'README.md')]
  const ignored = [join(tests, 'browser.mjs'), join(nested, 'helper.ts'), join(src, 'main.test.ts'), join(src, 'main.spec.ts'), ...outside]
  await Promise.all([...webFiles.map(file => writeFile(file, 'initial')),
    ...ignored.map(file => writeFile(file, 'export const value = 1'))])
  // 此处只验收监听，不启动 HTTP、WebSocket 或无关的依赖预构建。
  server = await createServer({ configFile: false, root: web, logLevel: 'silent',
    plugins: config.plugins?.filter(plugin => plugin && !Array.isArray(plugin) && 'name' in plugin && plugin.name === 'harness-web-watch-boundary'),
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, hmr: false, watch: { ...config.server?.watch, usePolling } } })
  // 模拟插件显式扩大监听范围，目录边界仍必须生效。
  server.watcher.add(outside)
  const changes: string[] = []
  server.watcher.on('change', file => changes.push(file))
  // getWatched 反映扫描结果，不能证明底层监听已完成初始化。
  // Vite 当前捆绑的 Chokidar 标记用于识别在 createServer 返回前已发出的 ready。
  const watcher = server.watcher as typeof server.watcher & { _readyEmitted: boolean }
  const ready = watcher._readyEmitted ? Promise.resolve() : new Promise<void>((resolve, reject) => {
    const done = () => { watcher.off('error', failed); resolve() }
    const failed = (error: Error) => { watcher.off('ready', done); reject(error) }
    watcher.once('ready', done)
    watcher.once('error', failed)
  })
  await ready
  await vi.waitFor(() => expect(server!.watcher.getWatched()[src]).toContain('main.ts'))
  await Promise.all([...webFiles.map(file => writeFile(file, 'updated content')),
    ...ignored.map(file => writeFile(file, 'export const value = 200'))])
  await vi.waitFor(() => {
    for (const file of webFiles) expect(changes).toContain(file)
  })
  expect(changes.filter(file => ignored.includes(file))).toEqual([])
  expect(server.watcher.getWatched()[nested]).toBeUndefined()
  expect(server.watcher.getWatched()[tests]).toBeUndefined()
  expect(server.watcher.getWatched()[desktop]).toBeUndefined()
  expect(server.watcher.getWatched()[root] ?? []).not.toContain('README.md')
  expect(server.watcher.getWatched()[src]).not.toContain('main.test.ts')
  expect(server.watcher.getWatched()[src]).not.toContain('main.spec.ts')
})
