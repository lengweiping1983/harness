import path from 'node:path'
import {createServer} from 'vite'
import {chromium} from 'playwright-core'
import {resolveBrowserExecutable} from './browser-executable.mjs'

export const webRoot = path.resolve(import.meta.dirname, '..')

export function launchBrowser(options = {}) {
  const executablePath = options.executablePath || (options.channel ? undefined : resolveBrowserExecutable())
  return chromium.launch({headless: true, ...options, executablePath})
}

/** 异步入口的 load 事件不代表夹具正文已执行，导航后等待显式就绪证据。 */
function synchronizeFixtureNavigation(browser, entry) {
  const prepared = new WeakSet()
  const preparePage = page => {
    if (prepared.has(page)) return page
    prepared.add(page)
    const ready = async () => {
      const fixture = await page.locator('script[type="module"][src]').evaluateAll((scripts, path) =>
        scripts.some(script => new URL(script.src, location.href).pathname === path), entry)
      if (fixture) await page.waitForFunction(() => window.__harnessFixtureReady === true)
    }
    for (const method of ['goto', 'reload']) {
      const navigate = page[method].bind(page)
      page[method] = async (...args) => {const response = await navigate(...args); await ready(); return response}
    }
    return page
  }
  const newPage = browser.newPage.bind(browser)
  browser.newPage = async (...args) => preparePage(await newPage(...args))
  const newContext = browser.newContext.bind(browser)
  browser.newContext = async (...args) => {
    const context = await newContext(...args)
    const createPage = context.newPage.bind(context)
    context.newPage = async (...options) => preparePage(await createPage(...options))
    context.on('page', preparePage)
    return context
  }
}

export async function browserScenario(run, {vite = {}, launch = {}, entry} = {}) {
  process.chdir(webRoot)
  let server, browser, result
  const failures = []
  try {
    server = await createServer({root: webRoot, configFile: path.join(webRoot, 'vite.config.ts'),
      ...vite,
      // 组件矩阵夹具在同步切换语言前准备双语；真实页面入口仍走按需加载。
      plugins: [...(vite.plugins ?? []), ...(entry ? [{name: 'fixture-locale-readiness', transform(code, id) {
        if (id.split('?')[0] !== entry) return
        return "import {loadLocale as prepareFixtureLocale} from '/src/i18n/index.ts';\nawait Promise.all([prepareFixtureLocale('zh-CN'), prepareFixtureLocale('en')]);\n" + code + "\nwindow.__harnessFixtureReady = true;\n"
      }}] : [])],
      server: {host: '127.0.0.1', port: 0, ...vite.server}})
    await server.listen()
    if (entry) {
      // 入口预转换属于服务准备；页面导航和业务操作仍使用原来的等待期限。
      let timer
      try {
        await Promise.race([
          (async () => {
            if (!await server.transformRequest(entry)) throw Error('Browser entry not found: ' + entry)
            await server.waitForRequestsIdle()
          })(),
          new Promise((_, reject) => {timer = setTimeout(() => reject(Error('Browser entry preparation timed out: ' + entry)), 120000)}),
        ])
      } finally {clearTimeout(timer)}
    }
    browser = await launchBrowser(launch)
    if (entry) synchronizeFixtureNavigation(browser, entry)
    result = await run({server, browser})
  } catch (error) {
    failures.push(error)
  }
  for (const resource of [browser, server]) {
    if (!resource) continue
    try {await resource.close()} catch (error) {failures.push(error)}
  }
  if (failures.length === 1) throw failures[0]
  if (failures.length > 1) throw new AggregateError(failures, 'Browser scenario and cleanup failed')
  return result
}
