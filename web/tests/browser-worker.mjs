import {pathToFileURL} from 'node:url'
import path from 'node:path'
import {createBrowserRuntime, installBrowserRuntime, webRoot} from './browser-runtime.mjs'

function errorRecord(error) {
  return {name: error?.name ?? 'Error', message: error?.message ?? String(error), stack: error?.stack,
    infrastructure: error?.browserInfrastructure === true,
    ...(error?.cause ? {cause: errorRecord(error.cause)} : {}),
    ...(error?.errors ? {errors: [...error.errors].map(errorRecord)} : {})}
}

const send = value => new Promise((resolve, reject) => process.send(value, error => error ? reject(error) : resolve()))
let runtime, busy = false
try {
  runtime = await createBrowserRuntime({executablePath: process.env.HARNESS_TEST_BROWSER})
  installBrowserRuntime(runtime)
  process.on('message', async message => {
    if (busy) return
    busy = true
    if (message.type === 'close') {
      try {await runtime.close(); await send({type: 'closed', stats: runtime.stats})}
      catch (error) {await send({type: 'closed', error: errorRecord(error), stats: runtime.stats})}
      finally {process.disconnect()}
      return
    }
    if (message.type !== 'run') {busy = false; return}
    const started = performance.now(), failures = []
    process.chdir(webRoot)
    try {await import(pathToFileURL(path.join(import.meta.dirname, message.name)).href)}
    catch (error) {failures.push(error)}
    try {await runtime.drainBrowsers()} catch (error) {failures.push(error)}
    busy = false
    const error = failures.length > 1 ? new AggregateError(failures, 'Scenario and resource cleanup failed') : failures[0]
    await send({type: 'result', name: message.name, durationMs: performance.now() - started,
      ...(error ? {error: errorRecord(error)} : {}), stats: {...runtime.stats}})
  })
  await send({type: 'ready', version: runtime.version})
} catch (error) {
  await send({type: 'fatal', error: errorRecord(error)})
  process.disconnect()
}
