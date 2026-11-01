import {existsSync} from 'node:fs'
import path from 'node:path'

export function developmentPython({root, env = process.env, platform = process.platform}) {
  const explicit = env.HARNESS_PYTHON
  if (explicit) return /[\\/]/.test(explicit) && !path.isAbsolute(explicit) ? path.resolve(root, explicit) : explicit
  const local = path.join(root, '.venv', platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  return existsSync(local) ? local : platform === 'win32' ? 'python' : 'python3'
}

export function pythonEnvironment(env = process.env) {
  return {...env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'}
}
