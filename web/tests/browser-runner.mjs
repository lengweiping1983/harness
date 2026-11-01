import {fork, execFile, execFileSync} from 'node:child_process'
import {createHash} from 'node:crypto'
import {createWriteStream, lstatSync, readFileSync, readdirSync} from 'node:fs'
import {mkdir, writeFile} from 'node:fs/promises'
import path from 'node:path'
import {selectBrowserScenarios} from '../../scripts/browser_selection.mjs'

const root = path.resolve(import.meta.dirname, '../..')

export function browserOptions(available, args) {
  const selections = [], options = {jobs: 1, timeoutMs: 300000, report: path.join(root, 'output/browser-regression.json')}
  const seen = new Set()
  for (const argument of args) {
    const match = /^--(jobs|timeout|report)=(.+)$/.exec(argument)
    if (!match) {selections.push(argument); continue}
    const [, key, value] = match
    if (seen.has(key)) throw Error('Duplicate browser option: ' + key)
    seen.add(key)
    if (key === 'report') {options.report = path.resolve(value); continue}
    if (!/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(Number(value))
      || (key === 'jobs' ? Number(value) > 8 : Number(value) > 3600)) throw Error('Invalid browser option: ' + argument)
    if (key === 'jobs') options.jobs = Number(value)
    else options.timeoutMs = Number(value) * 1000
  }
  return {...options, scenarios: selectBrowserScenarios(available, selections)}
}

export function isInfrastructureError(error) {
  return Boolean(error && (error.infrastructure || isInfrastructureError(error.cause) || error.errors?.some(isInfrastructureError)))
}

export function inputFingerprint() {
  const hash = createHash('sha256')
  hash.update(execFileSync('git', ['rev-parse', 'HEAD'], {cwd: root}))
  const files = execFileSync('git', ['ls-files', '--cached', '--others', '--exclude-standard', '-z'], {cwd: root})
    .toString('utf8').split('\0').filter(file => file && !file.split('/').includes('research'))
  for (const file of [...new Set(files)].sort()) {
    hash.update(file + '\0')
    try {
      const entry = lstatSync(path.join(root, file))
      if (!entry.isFile() || entry.isSymbolicLink()) throw Error('Browser input is not a regular file: ' + file)
      hash.update(readFileSync(path.join(root, file)))
    } catch (error) {
      if (error.code !== 'ENOENT') throw error
      hash.update('deleted')
    }
  }
  return hash.digest('hex')
}

export function buildFingerprint(directory = path.join(root, 'web/dist')) {
  const hash = createHash('sha256')
  function visit(current) {
    for (const entry of readdirSync(current, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(current, entry.name)
      if (entry.isDirectory()) visit(file)
      else {
        if (!entry.isFile() || entry.isSymbolicLink()) throw Error('Browser build is not a regular file: ' + file)
        hash.update(path.relative(directory, file) + '\0'); hash.update(readFileSync(file))
      }
    }
  }
  visit(directory)
  return hash.digest('hex')
}

export async function runScenarios(scenarios, {jobs, createWorker, onResult = () => {}}) {
  const results = scenarios.map(name => ({name, status: 'not_run'})), infrastructure = []
  let cursor = 0, stopped = false
  const workers = []
  const stop = error => {stopped = true; infrastructure.push(error)}
  await Promise.all(Array.from({length: Math.min(jobs, scenarios.length)}, async (_, index) => {
    let worker
    try {
      worker = await createWorker(index)
      workers.push(worker)
      while (!stopped && cursor < results.length) {
        const result = results[cursor++]
        result.status = 'running'
        try {
          const outcome = await worker.run(result.name)
          Object.assign(result, outcome, {status: outcome.error ? 'failed' : 'passed'})
          if (isInfrastructureError(outcome.error)) {result.status = 'infrastructure_failed'; stop(outcome.error)}
        } catch (error) {
          result.status = 'infrastructure_failed'; result.error = {message: error.message, infrastructure: true}; stop(result.error)
        }
        onResult(result)
      }
    } catch (error) {stop({message: error.message, infrastructure: true})}
    finally {
      if (worker) {
        try {await worker.close()} catch (error) {stop({message: error.message, infrastructure: true})}
      }
    }
  }))
  return {results, infrastructure, workers: workers.map(worker => worker.summary())}
}

async function terminateTree(child) {
  if (child.exitCode !== null || child.signalCode !== null) return
  if (process.platform === 'win32') {
    await new Promise((resolve, reject) => execFile('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], error => {
      if (error && child.exitCode === null && child.signalCode === null) reject(error); else resolve()
    }))
  } else {
    try {process.kill(-child.pid, 'SIGKILL')} catch (error) {if (error.code !== 'ESRCH') throw error}
  }
}

export async function startWorker(index, {executablePath, timeoutMs, logs,
  workerFile = path.join(import.meta.dirname, 'browser-worker.mjs')}) {
  await mkdir(logs, {recursive: true})
  const child = fork(workerFile, [], {cwd: path.join(root, 'web'),
    detached: process.platform !== 'win32', env: {...process.env, HARNESS_TEST_BROWSER: executablePath},
    stdio: ['ignore', 'pipe', 'pipe', 'ipc']})
  let pending, current, stream, lastStats, version, unhealthy = false
  const lifecycleLog = createWriteStream(path.join(logs, `worker-${index + 1}.log`))
  const log = (data, target) => {lifecycleLog.write(data); stream?.write(data); target.write(data)}
  child.stdout.on('data', data => log(data, process.stdout))
  child.stderr.on('data', data => log(data, process.stderr))
  const request = (type, value, timeout) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => {pending = undefined; reject(Error(`Browser worker ${index + 1} ${current ?? type} timed out`))}, timeout)
    pending = {type, resolve: message => {clearTimeout(timer); pending = undefined; resolve(message)},
      reject: error => {clearTimeout(timer); pending = undefined; reject(error)}}
    if (value) child.send(value, error => {if (error) pending?.reject(error)})
  })
  child.on('message', message => {
    if (message.stats) lastStats = message.stats
    if (message.type === 'fatal') pending?.reject(Error('Browser preflight failed: ' + JSON.stringify(message.error)))
    else if (message.type === pending?.type) pending.resolve(message)
  })
  let exit
  const exited = new Promise(resolve => {exit = resolve})
  child.on('error', error => pending?.reject(error))
  child.on('exit', (code, signal) => {pending?.reject(Error(`Browser worker exited (${code ?? signal}) during ${current ?? 'preflight'}`)); exit({code, signal})})
  let disposed = false
  const dispose = async () => {
    if (disposed) return
    disposed = true
    try {
      await terminateTree(child)
      let timer
      try {await Promise.race([exited, new Promise((_, reject) => {timer = setTimeout(() => reject(Error('Browser worker did not exit')), 10000)})])}
      finally {clearTimeout(timer)}
    } finally {stream?.end(); lifecycleLog.end()}
  }
  try {version = (await request('ready', null, 150000)).version}
  catch (error) {try {await dispose()} catch (cleanup) {throw new AggregateError([error, cleanup], 'Browser worker preflight and cleanup failed')}; throw error}
  return {
    async run(name) {
      current = name
      stream = createWriteStream(path.join(logs, name + '.log'))
      console.log(`\nCore browser [${index + 1}]: ${name}`)
      try {
        const outcome = await request('result', {type: 'run', name}, timeoutMs)
        if (outcome.name !== name) throw Error('Browser worker returned another scenario')
        if (outcome.error) {const message = JSON.stringify(outcome.error, null, 2); stream.write(message); console.error(message)}
        return {durationMs: outcome.durationMs, ...(outcome.error ? {error: outcome.error} : {})}
      } catch (error) {unhealthy = true; throw error}
      finally {stream.end(); stream = undefined; current = undefined}
    },
    async close() {
      try {
        if (unhealthy) {await dispose(); return}
        if (child.exitCode !== null || child.signalCode !== null) throw Error('Browser worker exited before cleanup')
        const outcome = await request('closed', {type: 'close'}, 45000)
        if (outcome.error) throw Error('Browser worker cleanup failed: ' + JSON.stringify(outcome.error))
        let timer
        try {
          const status = await Promise.race([exited, new Promise((_, reject) => {timer = setTimeout(() => reject(Error('Browser worker retained resources after cleanup')), 10000)})])
          if (status.code !== 0) throw Error('Browser worker cleanup exit failed: ' + JSON.stringify(status))
        } finally {clearTimeout(timer)}
      } finally {await dispose()}
    },
    summary: () => ({index: index + 1, version, ...lastStats}),
  }
}

export async function writeBrowserReport(file, value) {
  await mkdir(path.dirname(file), {recursive: true})
  await writeFile(file, JSON.stringify(value, null, 2) + '\n', 'utf8')
}
