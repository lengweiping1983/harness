import assert from 'node:assert/strict'
import {withDeadline} from './fixture-cleanup.mjs'

/** 路由回调的原始失败交回场景等待方，不让异步异常结束工作进程。 */
export async function interceptSaveReceipt(page, endpoint, mutate, submit) {
  let settle
  const handled = new Promise(resolve => {settle = resolve})
  const handler = async route => {
    let failure
    try {
      if (route.request().method() !== 'POST') {await route.fallback(); return}
      const response = await route.fetch(), value = await response.json()
      const detail = `Save interception (HTTP ${response.status()}): ${JSON.stringify(value)}`
      assert.equal(response.status(), 200, detail)
      assert.equal(value.status, 'ready', detail)
      assert.equal(typeof value.result?.source?.path, 'string', detail)
      assert.equal(typeof value.result.source.content, 'string', detail)
      mutate(value.result.source)
      await route.fulfill({response, json: value})
    } catch (error) {
      failure = error
      try {await route.abort('failed')}
      catch (cleanup) {failure = new AggregateError([error, cleanup], 'Save interception and abort failed')}
    }
    settle(failure)
  }
  await page.route(endpoint, handler)
  const failures = []
  try {
    await submit()
    const failure = await withDeadline(() => handled, 15000, 'Save receipt interception')
    if (failure) failures.push(failure)
  } catch (error) {failures.push(error)}
  try {await page.unroute(endpoint, handler)} catch (error) {failures.push(error)}
  if (failures.length === 1) throw failures[0]
  if (failures.length > 1) throw new AggregateError(failures, 'Save scenario and route cleanup failed')
}
