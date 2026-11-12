import {mkdtemp, rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {createServer} from 'vite'
import {chromium} from 'playwright-core'
import {withDeadline} from './fixture-cleanup.mjs'

export const webRoot = path.resolve(import.meta.dirname, '..')
let installed

export function installBrowserRuntime(runtime) {installed = runtime}
export function browserRuntime() {return installed}

export function infrastructureFailure(error) {
  // 保留原异常及其原因；分类不靠业务错误正文猜测。
  error.browserInfrastructure = true
  return error
}

export function fixtureLocalePlugin(entry) {
  return {name: 'fixture-locale-readiness', transform(code, id) {
    if (!entry || id.split('?')[0] !== entry) return
    return "import {loadLocale as prepareFixtureLocale} from '/src/i18n/index.ts';\nawait Promise.all([prepareFixtureLocale('zh-CN'), prepareFixtureLocale('en')]);\n"
      + code + '\nwindow.__harnessFixtureReady = true;\n'
  }}
}

// 只复用已知不修改配置或产品转换的夹具；新增插件能力默认走独立服务。
export function canShareFixture(vite, entry) {
  return Boolean(entry) && Object.keys(vite).every(key => key === 'plugins')
    && (vite.plugins ?? []).every(plugin => plugin && typeof plugin === 'object' && Object.getPrototypeOf(plugin) === Object.prototype
      && Object.keys(plugin).every(key => ['name', 'resolveId', 'load', 'configureServer'].includes(key))
      && ['resolveId', 'load', 'configureServer'].every(key => plugin[key] === undefined || typeof plugin[key] === 'function'))
}

/** 每个租约拥有独立路由表，结束后不能把旧闭包带入下一场景。 */
export function fixtureDispatcher() {
  let active
  const invoke = async function (hook, args) {
    for (const plugin of active?.plugins ?? []) {
      const value = await plugin[hook]?.apply(this, args)
      if (value != null) return value
    }
  }
  const plugin = {
    name: 'browser-fixture-dispatch',
    resolveId(...args) {return invoke.call(this, 'resolveId', args)},
    load(...args) {return invoke.call(this, 'load', args)},
    transform(code, id) {return fixtureLocalePlugin(active?.entry).transform(code, id)},
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const routes = active?.routes ?? []
        let index = 0
        const dispatch = error => {
          if (error) return next(error)
          const route = routes[index++]
          if (!route) return next()
          const url = request.url ?? '/'
          const pathname = url.split('?')[0]
          if (pathname !== route.prefix && !pathname.startsWith(route.prefix === '/' ? '/' : route.prefix + '/')) return dispatch()
          const suffix = url.slice(route.prefix.length)
          request.url = route.prefix === '/' ? url : suffix.startsWith('/') ? suffix : '/' + suffix
          const restore = failure => {request.url = url; dispatch(failure)}
          try {
            const result = route.handle(request, response, restore)
            if (result?.then) result.catch(restore)
          } catch (failure) {restore(failure)}
        }
        dispatch()
      })
    },
  }
  return {
    plugin,
    async attach(server, plugins, entry) {
      if (active) throw Error('Browser fixture already leased')
      const lease = {plugins, entry, routes: []}
      active = lease
      const middlewares = {use(prefix, handle) {
        if (typeof prefix === 'function') {handle = prefix; prefix = '/'}
        if (typeof prefix !== 'string' || typeof handle !== 'function') throw Error('Unsupported fixture middleware')
        lease.routes.push({prefix: prefix.replace(/\/$/, '') || '/', handle})
        return middlewares
      }}
      const facade = new Proxy(server, {get(target, key) {
        if (key === 'middlewares') return middlewares
        const value = Reflect.get(target, key)
        return typeof value === 'function' ? value.bind(target) : value
      }})
      try {
        for (const item of plugins) {
          if (await item.configureServer?.(facade) !== undefined) throw Error('Fixture post middleware requires a dedicated server')
        }
      } catch (error) {active = undefined; throw error}
      return async () => {
        // 页面已关闭后排空旧转换，再撤销路由并失效模块，避免同路径的夹具正文串用。
        try {await withDeadline(() => server.waitForRequestsIdle(), 30000, 'Fixture drain')}
        finally {active = undefined; server.moduleGraph.invalidateAll()}
      }
    },
  }
}

export async function prepareEntry(server, entry) {
  if (!entry) return
  await withDeadline(async () => {
    if (!await server.transformRequest(entry)) throw Error('Browser entry not found: ' + entry)
    await server.waitForRequestsIdle()
  }, 120000, 'Browser entry preparation: ' + entry)
}

export async function createFixtureServer(vite = {}, entry, cacheDir) {
  return createServer({root: webRoot, configFile: path.join(webRoot, 'vite.config.ts'), ...vite,
    // 每轮只拥有自己的临时缓存，不触碰开发服务的 node_modules/.vite。
    cacheDir,
    plugins: [...(vite.plugins ?? []), ...(entry ? [fixtureLocalePlugin(entry)] : [])],
    server: {host: '127.0.0.1', port: 0, watch: null, ...vite.server}})
}

export async function createBrowserRuntime({executablePath, serverFactory = createServer,
  browserFactory = chromium.launchServer.bind(chromium), connectFactory = chromium.connect.bind(chromium)} = {}) {
  const directory = await mkdtemp(path.join(tmpdir(), 'harness-browser-suite-'))
  let server, browserServer, leased = false, sequence = 0
  const browsers = new Set(), dispatcher = fixtureDispatcher()
  const stats = {viteStarts: 0, browserStarts: 0, sharedFixtures: 0, dedicatedFixtures: 0}
  const failures = []
  const attempt = async operation => {try {await operation()} catch (error) {failures.push(error)}}
  let closing
  const runtime = {
    directory, stats,
    async start() {
      browserServer = await browserFactory({headless: true, host: '127.0.0.1', executablePath})
      stats.browserStarts++
      const browser = await connectFactory(browserServer.wsEndpoint())
      try {runtime.version = browser.version()} finally {await browser.close()}
      server = await serverFactory({root: webRoot, configFile: path.join(webRoot, 'vite.config.ts'),
        cacheDir: path.join(directory, 'shared'), plugins: [dispatcher.plugin],
        server: {host: '127.0.0.1', port: 0, watch: null}})
      stats.viteStarts++
      await server.listen()
      // 扫描实际产品入口，使公共依赖准备故障发生在业务调度之前。
      await prepareEntry(server, '/src/main.tsx')
    },
    async connect() {
      if (closing) throw Error('Browser runtime is closing')
      const browser = await connectFactory(browserServer.wsEndpoint())
      browsers.add(browser)
      browser.once('disconnected', () => browsers.delete(browser))
      return browser
    },
    async fixture(vite, entry) {
      if (closing || leased) throw Error('Browser runtime is unavailable or already leased')
      leased = true
      let own, detach
      try {
        if (canShareFixture(vite, entry)) {
          detach = await dispatcher.attach(server, vite.plugins ?? [], entry)
          stats.sharedFixtures++
        } else {
          own = await createFixtureServer(vite, entry, path.join(directory, 'fixture-' + ++sequence))
          stats.viteStarts++; stats.dedicatedFixtures++
          await own.listen()
        }
        return {server: own ?? server, close: async () => {
          try {if (own) await withDeadline(() => own.close(), 10000, 'Vite cleanup'); else await detach()}
          finally {leased = false}
        }}
      } catch (error) {
        leased = false
        const errors = [error]
        try {if (own) await own.close(); else await detach?.()} catch (failure) {errors.push(failure)}
        throw infrastructureFailure(errors.length === 1 ? error : new AggregateError(errors, 'Fixture preparation and cleanup failed'))
      }
    },
    async drainBrowsers() {
      const errors = []
      for (const browser of [...browsers]) {
        try {await withDeadline(() => browser.close(), 10000, 'Browser connection cleanup')} catch (error) {errors.push(error)}
      }
      if (errors.length) throw infrastructureFailure(new AggregateError(errors, 'Browser connections did not close'))
    },
    close() {
      return closing ??= (async () => {
        await attempt(() => runtime.drainBrowsers())
        if (server) await attempt(() => withDeadline(() => server.close(), 10000, 'Shared Vite cleanup'))
        if (browserServer) await attempt(() => withDeadline(() => browserServer.close(), 10000, 'Chromium cleanup'))
        // 只在所属服务确认关闭后删除本轮 mkdtemp 目录；失败保留原目录供诊断。
        if (!failures.length) await attempt(() => rm(directory, {recursive: true, force: true}))
        if (failures.length) throw infrastructureFailure(new AggregateError(failures, 'Browser runtime cleanup failed: ' + directory))
      })()
    },
  }
  try {await runtime.start(); return runtime}
  catch (error) {
    try {await runtime.close()} catch (cleanup) {throw infrastructureFailure(new AggregateError([error, cleanup], 'Browser preflight and cleanup failed'))}
    throw infrastructureFailure(error)
  }
}
