const collectors = new WeakMap()
const limit = 8, bodyLimit = 65536

/** 只观察现有执行响应，不查询、重放或按输出正文修改失败分类。 */
export function observeAcademyExecutions(browser) {
  if (collectors.has(browser)) return browser
  const contexts = new Map(), records = [], pending = new Set()
  let closed = false
  const observe = context => {
    if (contexts.has(context)) return
    const response = value => {
      const request = value.request()
      const pathname = new URL(value.url()).pathname
      const method = request.method()
      const execution = method === 'POST' && /^\/api\/v1\/academy\/projects\/[^/]+\/(run|check|tutor-turns)$/.test(pathname)
      const recovery = method === 'GET' && (/^\/api\/v1\/academy\/receipts\/[^/]+$/.test(pathname)
        || /^\/api\/v1\/academy\/projects\/[^/]+\/(runs|tutor-turns)$/.test(pathname))
      if (!execution && !recovery) return
      const record = {url: value.url(), method, status: value.status(), state: 'pending'}
      records.push(record)
      if (records.length > limit) records.shift()
      const reading = (async () => {
        try {
          const body = await value.text()
          if (!closed) Object.assign(record, {state: 'received', body: body.slice(0, bodyLimit), truncated: body.length > bodyLimit})
        } catch (error) {
          if (!closed) Object.assign(record, {state: 'unavailable', error: error.message})
        }
      })()
      pending.add(reading)
      void reading.finally(() => pending.delete(reading))
    }
    contexts.set(context, response)
    context.on('response', response)
  }
  for (const context of browser.contexts()) observe(context)
  const newContext = browser.newContext.bind(browser), newPage = browser.newPage.bind(browser)
  browser.newContext = async (...args) => {const context = await newContext(...args); observe(context); return context}
  browser.newPage = async (...args) => {const page = await newPage(...args); observe(page.context()); return page}
  const collector = {
    wait: () => Promise.all([...pending]),
    snapshot: () => records.map(record => ({...record})),
    close() {
      closed = true
      for (const [context, listener] of contexts) context.off('response', listener)
      contexts.clear()
      collectors.delete(browser)
      browser.off('disconnected', collector.close)
    },
  }
  collectors.set(browser, collector)
  browser.once('disconnected', collector.close)
  return browser
}

export function academyDiagnostics(browser) {return collectors.get(browser)}
