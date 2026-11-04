import {readdirSync} from 'node:fs'
import path from 'node:path'
import {resolveBrowserExecutable} from './browser-executable.mjs'
import {browserOptions, buildFingerprint, inputFingerprint, inputIdentity, runScenarios, startWorker, writeBrowserReport} from './browser-runner.mjs'

const scripts = readdirSync(import.meta.dirname).filter(name => name.endsWith('-smoke.mjs')).sort()
const options = browserOptions(scripts, process.argv.slice(2))
const started = new Date(), startedAt = performance.now()
const report = {started: started.toISOString(), platform: process.platform, node: process.version,
  jobs: options.jobs, timeoutMs: options.timeoutMs, passed: false, inputsUnchanged: false, buildUnchanged: false,
  results: options.scenarios.map(name => ({name, status: 'not_run'}))}
const logs = path.resolve(import.meta.dirname, '../../output/browser-logs', started.toISOString().replace(/[:.]/g, '-'))
await writeBrowserReport(options.report, report)
try {
  report.source = inputIdentity()
  report.input = report.source.fingerprint
  report.build = buildFingerprint()
  report.executablePath = resolveBrowserExecutable()
  console.log(`Core browser executable: ${report.executablePath}`)
  const outcome = await runScenarios(options.scenarios, {jobs: options.jobs,
    createWorker: index => startWorker(index, {executablePath: report.executablePath, timeoutMs: options.timeoutMs, logs}),
    onResult: result => console.log(`Core browser ${result.status}: ${result.name} (${((result.durationMs ?? 0) / 1000).toFixed(2)}s)`),
  })
  Object.assign(report, outcome)
  report.inputsUnchanged = report.input === inputFingerprint()
  report.buildUnchanged = report.build === buildFingerprint()
  report.passed = report.results.every(result => result.status === 'passed') && !report.infrastructure.length
    && report.inputsUnchanged && report.buildUnchanged
} catch (error) {report.error = {message: error.message, stack: error.stack}; console.error(error)}
finally {
  report.durationMs = performance.now() - startedAt
  report.finished = new Date().toISOString()
  report.logs = logs
  await writeBrowserReport(options.report, report)
}
const count = status => report.results.filter(result => result.status === status).length
console.log(`\nCore browser: ${count('passed')}/${report.results.length} passed; ${count('failed')} failed; ${count('infrastructure_failed')} infrastructure failed; ${count('not_run')} not run (${(report.durationMs / 1000).toFixed(2)}s)`)
console.log('Browser report:', options.report)
if (!report.inputsUnchanged || !report.buildUnchanged) console.error('Browser input/build identity was changed or unverified')
if (report.infrastructure?.length) console.error('Browser infrastructure failed:', JSON.stringify(report.infrastructure))
if (!report.passed) process.exitCode = 1
