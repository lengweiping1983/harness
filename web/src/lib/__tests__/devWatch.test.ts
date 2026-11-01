import { afterEach, expect, it, vi } from 'vitest'
import { createServer, loadConfigFromFile, type ViteDevServer } from 'vite'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

let server: ViteDevServer | undefined
let root: string | undefined

afterEach(async () => {
  await server?.close()
  if (root) await rm(root, { recursive: true, force: true })
  server = undefined
  root = undefined
})

it('tracks application changes while ignoring test directories and test modules', async () => {
  const loaded = await loadConfigFromFile({ command: 'serve', mode: 'development' }, fileURLToPath(new URL('../../../vite.config.ts', import.meta.url)))
  if (!loaded) throw new Error('无法加载开发配置')
  const config = loaded.config
  root = await mkdtemp(join(tmpdir(), 'harness-dev-watch-'))
  const src = join(root, 'src')
  const tests = join(root, 'tests')
  const nested = join(src, '__tests__')
  await Promise.all([mkdir(nested, { recursive: true }), mkdir(tests)])
  const application = join(src, 'main.ts')
  const ignored = [join(tests, 'browser.mjs'), join(nested, 'helper.ts'), join(src, 'main.test.ts'), join(src, 'main.spec.ts')]
  await Promise.all([writeFile(join(root, 'index.html'), '<html></html>'), writeFile(application, 'export const value = 1'),
    ...ignored.map(file => writeFile(file, 'export const value = 1'))])
  server = await createServer({ configFile: false, root, logLevel: 'silent',
    server: { port: 0, watch: config.server?.watch } })
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
  await server.listen()
  await ready
  await vi.waitFor(() => expect(server!.watcher.getWatched()[src]).toContain('main.ts'))
  await Promise.all([writeFile(application, 'export const value = 200'),
    ...ignored.map(file => writeFile(file, 'export const value = 200'))])
  await vi.waitFor(() => expect(changes).toContain(application))
  expect(changes.filter(file => ignored.includes(file))).toEqual([])
  expect(server.watcher.getWatched()[nested]).toBeUndefined()
  expect(server.watcher.getWatched()[tests]).toBeUndefined()
})
