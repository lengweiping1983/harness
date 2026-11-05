import assert from 'node:assert/strict'
import {execFile} from 'node:child_process'
import {promisify} from 'node:util'
import {fileURLToPath} from 'node:url'

const execute = promisify(execFile)
const root = fileURLToPath(new URL('../../', import.meta.url))
const marker = 'harness-academy-sandbox-ready\n'

async function probeSandbox() {
  const {fixturePython} = await import('./python-fixture.mjs')
  try {
    const result = await execute(fixturePython, ['scripts/check_academy_sandbox.py'], {cwd: root, encoding: 'utf8', maxBuffer: 1024 * 1024})
    return {code: 0, ...result}
  } catch (error) {
    if (!Number.isInteger(error.code) || error.signal || error.killed) throw error
    return {code: error.code, stdout: error.stdout, stderr: error.stderr}
  }
}

function probeFailure(raw, infrastructure) {
  const error = Error(`Academy sandbox launch probe failed (exit ${raw.code}): ${raw.stdout}\n${raw.stderr}`)
  error.probe = raw
  error.browserInfrastructure = infrastructure
  return error
}

/** 同一验证工作进程拥有一次预检；成功、失败及并发等待均不重放探针。 */
export function createAcademySandboxCheck({probe = probeSandbox} = {}) {
  let pending, evidence = null
  const launch = async () => {
    evidence = {status: 'running', started: new Date().toISOString()}
    try {
      const receipt = await probe()
      evidence = {...evidence, status: 'completed', receipt: structuredClone(receipt)}
      return receipt
    } catch (error) {
      evidence = {...evidence, status: 'error', error: {name: error?.name ?? 'Error', message: error?.message ?? String(error), stack: error?.stack}}
      throw error
    } finally {evidence.finished = new Date().toISOString()}
  }
  const check = async project => {
    assert.ok(['seatbelt', 'bubblewrap', 'unavailable'].includes(project?.sandbox), 'Academy project must report its current sandbox backend')
    if (project.sandbox === 'unavailable') {
      const error = Error('Academy execution requires an available OS sandbox (Linux bubblewrap or macOS seatbelt); this environment is not verified')
      error.browserInfrastructure = true
      throw error
    }
    const raw = await (pending ??= Promise.resolve().then(launch))
    assert.ok(Number.isInteger(raw?.code) && typeof raw.stdout === 'string' && typeof raw.stderr === 'string', 'Invalid Academy probe process receipt')
    const report = JSON.parse(raw.stdout)
    assert.equal(typeof report?.passed, 'boolean', 'Invalid Academy probe result')
    if (report.reason === 'sandbox-probe-error') {
      assert.equal(report.passed, false)
      assert.equal(raw.code, 1)
      throw probeFailure(raw, false)
    }
    assert.equal(report.backend, project.sandbox, 'Academy probe and project must use the same sandbox backend')
    const result = report.execution
    assert.ok(result && ['passed', 'failed', 'timeout'].includes(result.status) && Number.isInteger(result.exit_code)
      && typeof result.stdout === 'string' && typeof result.stderr === 'string' && typeof result.truncated === 'boolean'
      && typeof result.memory_limited === 'boolean', 'Invalid Academy probe execution receipt')
    assert.equal(result.sandbox, project.sandbox)
    const passed = result.status === 'passed' && result.exit_code === 0 && result.stdout === marker && result.stderr === ''
      && !result.truncated && !result.memory_limited
    assert.equal(report.passed, passed, 'Inconsistent Academy probe result')
    assert.equal(report.reason, passed ? 'ready' : 'sandbox-probe-failed')
    assert.equal(raw.code, passed ? 0 : 1, 'Academy probe exit code must match its report')
    assert.equal(raw.stderr, '', 'Academy probe must not emit unreported errors')
    if (!passed) throw probeFailure(raw, true)
  }
  check.snapshot = () => structuredClone(evidence)
  return check
}

export const requireAcademySandbox = createAcademySandboxCheck()
