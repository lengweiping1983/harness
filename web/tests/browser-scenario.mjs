import {mkdtemp, rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {chromium} from 'playwright-core'
import {resolveBrowserExecutable} from './browser-executable.mjs'
import {browserRuntime, createFixtureServer, infrastructureFailure, prepareEntry, webRoot} from './browser-runtime.mjs'
import {withDeadline} from './fixture-cleanup.mjs'
import {observeAcademyExecutions} from './academy-diagnostics.mjs'

export {webRoot}

export async function launchBrowser(options = {}) {
  if (browserRuntime() && !Object.keys(options).length) return observeAcademyExecutions(await browserRuntime().connect())
  const executablePath = options.executablePath || (options.channel ? undefined : resolveBrowserExecutable())
  return observeAcademyExecutions(await chromium.launch({headless: true, ...options, executablePath}))
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
  let server, browser, result, lease, directory, phase = 'prepare'
  const failures = []
  try {
    if (browserRuntime()) {
      lease = await browserRuntime().fixture(vite, entry)
      server = lease.server
    } else {
      directory = await mkdtemp(path.join(tmpdir(), 'harness-browser-fixture-'))
      server = await createFixtureServer(vite, entry, directory)
      await server.listen()
    }
    await prepareEntry(server, entry)
    browser = await launchBrowser(launch)
    if (entry) synchronizeFixtureNavigation(browser, entry)
    phase = 'scenario'
    result = await run({server, browser})
  } catch (error) {
    failures.push(phase === 'prepare' ? infrastructureFailure(error) : error)
  }
  for (const [resource, label] of [[browser, 'Browser'], [lease ?? server, 'Vite']]) {
    if (resource) {
      try {await withDeadline(() => resource.close(), 30000, label + ' fixture cleanup')}
      catch (error) {failures.push(infrastructureFailure(error))}
    }
  }
  if (directory && !failures.some(error => error.browserInfrastructure)) {
    try {await rm(directory, {recursive: true, force: true})} catch (error) {failures.push(infrastructureFailure(error))}
  }
  if (failures.length === 1) throw failures[0]
  if (failures.length > 1) throw new AggregateError(failures, 'Browser scenario and cleanup failed')
  return result
}
