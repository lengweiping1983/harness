import assert from 'node:assert/strict'
import {mkdtempSync, mkdirSync, writeFileSync, rmSync} from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {test} from 'node:test'
import {backendLaunch} from '../backend.mjs'
import {developmentPython} from '../python-runtime.mjs'

for (const platform of ['darwin', 'win32']) {
  test(platform + ': packaged runtime isolates Python home and keeps caller environment unchanged', t => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'harness-runtime-'))
    t.after(() => rmSync(root, {recursive: true, force: true}))
    const resources = path.join(root, 'resources')
    const runtime = path.join(resources, 'runtime')
    const python = path.join(runtime, 'python', platform === 'win32' ? 'python.exe' : 'bin/python3')
    mkdirSync(path.dirname(python), {recursive: true})
    mkdirSync(path.join(runtime, 'web'), {recursive: true})
    writeFileSync(python, '')
    writeFileSync(path.join(runtime, 'web', 'index.html'), '')
    const env = {PYTHONUTF8:'0', PYTHONIOENCODING:'gbk', PYTHONHOME: '/unrelated/python', PYTHONPATH: '/unrelated/site', HARNESS_PYTHON: 'wrong-python', ...(platform === 'win32' ? {PythonHome: 'C:\\unrelated\\python'} : {}), KEEP: 'value'}
    const launch = backendLaunch({root, resources, env, platform})
    assert.equal(launch.python, python)
    assert.equal(launch.env.PYTHONHOME, undefined)
    if (platform === 'win32') assert.equal(launch.env.PythonHome, undefined)
    assert.equal(launch.env.PYTHONPATH, path.join(runtime, 'site'))
    assert.equal(launch.env.PYTHONNOUSERSITE, '1')
    assert.equal(launch.env.KEEP, 'value')
    assert.equal(launch.env.PYTHONUTF8, '1')
    assert.equal(launch.env.PYTHONIOENCODING, 'utf-8')
    assert.equal(env.PYTHONUTF8, '0')
    assert.equal(env.PYTHONHOME, '/unrelated/python')
  })

  test(platform + ': missing packaged runtime fails without falling back to system Python', t => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'harness-runtime-missing-'))
    t.after(() => rmSync(root, {recursive: true, force: true}))
    assert.throws(() => backendLaunch({root, resources: root, env: {HARNESS_PYTHON: 'python'}, platform}), /runtime is incomplete/)
  })

  test(platform + ': development prefers explicit Python and the platform virtual environment', t => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'harness-source-'))
    t.after(() => rmSync(root, {recursive: true, force: true}))
    const python = path.join(root, '.venv', platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
    mkdirSync(path.dirname(python), {recursive: true})
    writeFileSync(python, '')
    assert.equal(developmentPython({root, env: {}, platform}), python)
    assert.equal(developmentPython({root, env: {HARNESS_PYTHON: './custom/python'}, platform}), path.resolve(root, './custom/python'))
    assert.equal(developmentPython({root, env: {HARNESS_PYTHON: 'custom-python'}, platform}), 'custom-python')
  })
}
