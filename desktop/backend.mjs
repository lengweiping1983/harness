import {stopChild} from './stop-child.mjs'
import {developmentPython, pythonEnvironment} from './python-runtime.mjs'
import net from 'node:net'
import {existsSync} from 'node:fs'
import path from 'node:path'
import {spawn} from 'node:child_process'
export async function availablePort() {
  const server = net.createServer()
  await new Promise((resolve, reject) => server.once('error', reject).listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  await new Promise(resolve => server.close(resolve))
  return port
}
export function backendLaunch({root, resources, env = process.env, platform = process.platform}) {
  if (resources) {
    const runtime = path.join(resources, 'runtime')
    const python = path.join(runtime, 'python', platform === 'win32' ? 'python.exe' : 'bin/python3')
    if (!existsSync(python) || !existsSync(path.join(runtime, 'web', 'index.html'))) throw Error('Packaged Harness runtime is incomplete. Reinstall the application.')
    const environment = {...pythonEnvironment(env), PYTHONPATH:path.join(runtime, 'site'), PYTHONNOUSERSITE:'1', PYTHONDONTWRITEBYTECODE:'1', HARNESS_WEB_DIST:path.join(runtime, 'web')}
    // 安装包只使用自带标准库，不能继承启动终端的 Python 安装根。
    for (const key of Object.keys(environment)) {
      if (platform === 'win32' ? key.toUpperCase() === 'PYTHONHOME' : key === 'PYTHONHOME') delete environment[key]
    }
    return {python, cwd:root, env:environment}
  }
  return {python:developmentPython({root, env, platform}), cwd:root, env:pythonEnvironment(env)}
}
export async function startBackend({root, resources, bridge, cdp, port: requestedPort}) {
  const port = requestedPort || await availablePort()
  const launch = backendLaunch({root, resources})
  const child = spawn(launch.python, ['-X', 'utf8', '-m', 'harness.cli', '--ui', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: launch.cwd, env: {...launch.env, HARNESS_NODE: process.execPath, HARNESS_DESKTOP_BRIDGE: bridge.url, HARNESS_DESKTOP_TOKEN: bridge.token, HARNESS_DESKTOP_CDP: cdp}, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let failure = null
  let diagnostics = ''
  child.once('error', error => {failure = error})
  child.stderr.setEncoding('utf8')
  child.stderr.on('data', data => {diagnostics = (diagnostics + data).slice(-4000)})
  child.stdout.on('data', () => {})
  const url = `http://127.0.0.1:${port}`
  try {
    for (let attempt = 0; attempt < 900; attempt++) {
      if (failure || child.exitCode !== null) throw failure || Error('Harness backend exited: ' + diagnostics)
      try {const response = await fetch(url + '/api/v1/desktop/health', {headers:{Authorization:'Bearer ' + bridge.token}, signal: AbortSignal.timeout(500)}); if (response.ok && (await response.json()).ready === true) return {url, port, child}} catch {}
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    throw Error('Harness backend did not become ready: ' + diagnostics)
  } catch (error) {await stopChild(child); throw error}
}
