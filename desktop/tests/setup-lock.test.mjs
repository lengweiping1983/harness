import assert from 'node:assert/strict'
import {test} from 'node:test'
import {mkdtemp, readFile, writeFile, rm} from 'node:fs/promises'
import {spawn, spawnSync} from 'node:child_process'
import {once} from 'node:events'
import {fileURLToPath} from 'node:url'
import {developmentPython} from '../python-runtime.mjs'
import os from 'node:os'
import path from 'node:path'
import {acquireSetupLock as acquireLock} from '../setup-lock.mjs'
import {setupCopy} from '../setup-copy.mjs'
const acquireSetupLock = (python, filename, options = {}) => acquireLock(python, filename, {copy:setupCopy('en'), ...options})
import {nodeVersionSupported, prepareDesktop as prepare} from '../setup.mjs'
const prepareDesktop = options => prepare({language:'en', ...options})

const python = developmentPython({root:fileURLToPath(new URL('../../', import.meta.url))})
async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'harness-lock-'))
  t.after(() => rm(root, {recursive:true, force:true}))
  return path.join(root, 'prepare.lock')
}

test('OS lock excludes competitors, releases once and preserves old metadata', async t => {
  const filename = await fixture(t)
  const metadata = JSON.stringify({pid:process.pid, id:'old-owner'})
  await writeFile(filename, metadata)
  const lock = await acquireSetupLock(python, filename)
  try {
    const competitors = await Promise.allSettled(Array.from({length:4}, () => acquireSetupLock(python, filename)))
    assert.ok(competitors.every(result => result.status === 'rejected' && /already running/.test(result.reason.message)))
  } finally {await Promise.all([lock.release(), lock.release()])}
  // Windows 字节锁也阻止读取该字节；释放后核对竞争者没有改写原元数据。
  assert.equal(await readFile(filename, 'utf8'), metadata)
  const next = await acquireSetupLock(python, filename)
  await next.release()
  assert.equal(await readFile(filename, 'utf8'), metadata)
})

test('owner process death releases kernel lock without deleting lock file', async t => {
  const filename = await fixture(t)
  await writeFile(filename, 'persistent-owner-metadata')
  const module = new URL('../setup-lock.mjs', import.meta.url).href
  const owner = spawn(process.execPath, ['--input-type=module', '-e', `
    import {acquireSetupLock} from ${JSON.stringify(module)};
    await acquireSetupLock(process.argv[1], process.argv[2]);
    console.log('ready');
  `, python, filename], {stdio:['ignore','pipe','pipe']})
  t.after(() => {if (owner.exitCode === null && owner.signalCode === null) owner.kill('SIGKILL')})
  await Promise.race([once(owner.stdout, 'data'), once(owner, 'exit').then(() => {throw Error('Owner exited before lock')})])
  await assert.rejects(acquireSetupLock(python, filename), /already running/)
  const exited = once(owner, 'exit')
  owner.kill('SIGKILL')
  await exited
  // The helper sees EOF after parent death; wait through its bounded exit window.
  let next
  for (let attempt = 0; attempt < 20; attempt++) {
    try {next = await acquireSetupLock(python, filename); break}
    catch (error) {if (attempt === 19) throw error; await new Promise(resolve => setTimeout(resolve, 25))}
  }
  await next.release()
  assert.equal(await readFile(filename, 'utf8'), 'persistent-owner-metadata')
})

test('interpreter startup failure rejects without reporting ownership', async t => {
  const filename = await fixture(t)
  await assert.rejects(acquireSetupLock(path.join(path.dirname(filename), 'missing-python'), filename), /ENOENT/)
})

test('preparation failure releases its lock before returning the error', async t => {
  const filename = await fixture(t)
  const root = path.dirname(filename)
  const resolved = spawnSync(python, ['-c', 'import sys; print(sys.executable)'], {encoding:'utf8'})
  assert.equal(resolved.status, 0)
  const interpreter = resolved.stdout.trim()
  const calls = []
  const run = async (command, args) => {
    calls.push([command, args])
    if (args.includes('import sys; print(sys.executable)')) return interpreter
    if (args.includes('venv')) throw Error('fixture venv failure')
    return ''
  }
  await assert.rejects(prepareDesktop({root, env:{...process.env, HARNESS_PYTHON:interpreter}, run, notify:() => {}, nodeVersion:'22.23.2'}), /fixture venv failure/)
  const lock = await acquireSetupLock(interpreter, path.join(root, '.run', 'desktop-prepare.lock'))
  await lock.release()
  assert.equal(calls.filter(([, args]) => args.includes('venv')).length, 1)
})


test('preparation rejects an interpreter older than Python 3.14 before setup', async t => {
  const filename = await fixture(t)
  const root = path.dirname(filename)
  const calls = []
  const run = async (command, args) => {
    calls.push([command, args])
    assert.ok(args.includes('import sys; sys.exit(0 if sys.version_info[:2] >= (3, 14) else 1)'))
    throw Error('unsupported Python version')
  }
  await assert.rejects(prepareDesktop({root, env:{...process.env, HARNESS_PYTHON:'fixture-python'}, run, notify:() => {}, nodeVersion:'22.23.2'}), /Python 3\.14 or newer was not found/)
  assert.equal(calls.length, 1)
})

test('preparation requires Node.js 22 or newer', async t => {
  const filename = await fixture(t)
  const root = path.dirname(filename)
  const run = async () => {throw Error('preparation must not start on an unsupported Node')}
  await assert.rejects(prepareDesktop({root, env:{...process.env, HARNESS_PYTHON:'fixture-python'}, run, notify:() => {}, nodeVersion:'21.12.0'}), /Node\.js 22 or newer is required/)
  await assert.rejects(prepareDesktop({root, env:{...process.env, HARNESS_PYTHON:'fixture-python'}, run, notify:() => {}, nodeVersion:'20.0.0'}), /Node\.js 22 or newer is required/)
  assert.equal(nodeVersionSupported('21.12.0'), false)
  assert.equal(nodeVersionSupported('22.0.0'), true)
  assert.equal(nodeVersionSupported('22.23.2'), true)
  assert.equal(nodeVersionSupported('23.0.0'), true)
})

test('lock contention follows explicit resource language without changing lock ownership',async t=>{
 const filename=await fixture(t)
 const owner=await acquireSetupLock(python,filename)
 try {
  for(const language of ['en','zh-CN','en']){
   const copy=setupCopy(language)
   await assert.rejects(acquireSetupLock(python,filename,{copy}),error=>{
    assert.equal(error.message,copy('desktop.setup.lockBusy'))
    assert.equal(/[\u4e00-\u9fff]/.test(error.message),language==='zh-CN')
    return true
   })
  }
 }finally{await owner.release()}
 const next=await acquireSetupLock(python,filename)
 await next.release()
})
