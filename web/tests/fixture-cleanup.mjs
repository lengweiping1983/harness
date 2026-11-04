import {stopChild} from '../../desktop/stop-child.mjs'
import {academyDiagnostics} from './academy-diagnostics.mjs'

export async function withDeadline(operation, timeoutMs, label) {
  let timer
  try {
    return await Promise.race([
      Promise.resolve().then(operation),
      new Promise((_, reject) => {timer = setTimeout(() => reject(Error(label + ' timed out')), timeoutMs)}),
    ])
  } finally {clearTimeout(timer)}
}

export async function cleanupFixture({error, browser, lines, backend, releases = [], closeMs = 10000,
  graceMs = 20000, killMs = 5000, diagnosticMs = 3000}) {
  const failures = error ? [error] : []
  const attempt = async operation => {
    try {await operation()} catch (failure) {failures.push(failure)}
  }
  for (const release of releases) await attempt(release)
  const diagnostics = academyDiagnostics(browser)
  if (diagnostics) {
    try {
      if (error) {
        try {await withDeadline(() => diagnostics.wait(), diagnosticMs, 'Academy response diagnostics')}
        catch (failure) {console.error('Academy response diagnostics unavailable:', failure)}
        console.error('Academy execution receipts:', JSON.stringify(diagnostics.snapshot()))
      }
    } finally {diagnostics.close()}
  }
  if (browser) await attempt(() => withDeadline(() => browser.close(), closeMs, 'Browser cleanup'))
  if (lines) await attempt(() => lines.close())
  if (backend) await attempt(() => stopChild(backend, {graceMs, killMs}))
  if (failures.length === 1) throw failures[0]
  if (failures.length > 1) throw new AggregateError(failures, 'Fixture operation and cleanup failed')
}

export async function diagnoseFailure(error, operations, {timeoutMs = 3000, report = console.error} = {}) {
  for (const operation of operations) {
    try {await withDeadline(operation, timeoutMs, 'Failure diagnostics')}
    catch (diagnosticError) {report('Failure diagnostics unavailable:', diagnosticError)}
  }
  throw error
}
