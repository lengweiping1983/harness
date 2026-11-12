import {buildArtifacts} from './build-artifacts.mjs'
import {setupCopy} from './setup-copy.mjs'
import {developmentPython} from './python-runtime.mjs'
import {spawn} from 'node:child_process'
import {acquireSetupLock} from './setup-lock.mjs'
import {createHash, randomUUID} from 'node:crypto'
import {existsSync, realpathSync} from 'node:fs'
import {mkdir, readFile, readdir, writeFile, rename, lstat} from 'node:fs/promises'
import path from 'node:path'

export function runCommand(command, args, {cwd, env = process.env, capture = false, language = env.HARNESS_LANGUAGE} = {}) {
  const copy = setupCopy(language, {env})
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {cwd, env, stdio:capture ? ['ignore', 'pipe', 'pipe'] : 'inherit'})
    let output = ''
    if (capture) {
      child.stdout.on('data', data => {output = (output + data).slice(-8000)})
      child.stderr.on('data', data => {output = (output + data).slice(-8000)})
    }
    child.once('error', reject)
    child.once('exit', (code, signal) => code === 0 ? resolve(output) : reject(Error(copy(capture ? 'desktop.setup.commandExitedOutput' : 'desktop.setup.commandExited', {command: path.basename(command), status: signal || code, output: output.trim()}))))
  })
}

export function npmCLI(env = process.env, copy = setupCopy(env.HARNESS_LANGUAGE, {env})) {
  const bin = path.dirname(process.execPath)
  const candidates = [env.npm_execpath, path.join(bin, 'node_modules/npm/bin/npm-cli.js'), path.resolve(bin, '../lib/node_modules/npm/bin/npm-cli.js')]
  for (const directory of (env.PATH || '').split(path.delimiter)) {
    try {
      const executable = realpathSync(path.join(directory, process.platform === 'win32' ? 'npm.cmd' : 'npm'))
      candidates.push(executable.endsWith('.js') ? executable : path.join(path.dirname(executable), 'node_modules/npm/bin/npm-cli.js'))
    } catch {}
  }
  const found = candidates.find(candidate => candidate && candidate.endsWith('npm-cli.js') && existsSync(candidate))
  if (!found) throw Error(copy('desktop.setup.npmMissing'))
  return found
}

async function fingerprint(root, entries) {
  const hash = createHash('sha256')
  async function add(relative) {
    const filename = path.join(root, relative)
    if (!existsSync(filename)) {hash.update(relative + ':missing'); return}
    const stat = await lstat(filename)
    if (stat.isDirectory()) {
      for (const name of (await readdir(filename)).sort()) {
        if (name !== 'research') await add(path.join(relative, name))
      }
    } else if (stat.isFile()) {hash.update(relative); hash.update(await readFile(filename))}
  }
  for (const entry of entries) await add(entry)
  hash.update(`${process.platform}:${process.arch}:${process.versions.node.split('.')[0]}`)
  return hash.digest('hex')
}

export function nodeVersionSupported(version = process.versions.node) {
  const [major] = version.split('.').map(Number)
  return major >= 22
}

export async function prepareDesktop({root, env = process.env, run: execute = runCommand, notify = console.log, nodeVersion = process.versions.node, language = env.HARNESS_LANGUAGE} = {}) {
  const copy = setupCopy(language, {env})
  const run = (command, args, options) => execute(command, args, {...options, language})
  if (!nodeVersionSupported(nodeVersion)) throw Error(copy('desktop.setup.nodeRequired'))
  const npm = npmCLI(env, copy)
  const runtime = path.join(root, '.run')
  await mkdir(runtime, {recursive:true})
  const python = path.join(runtime, 'desktop-python', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const candidates = env.HARNESS_PYTHON ? [[developmentPython({root, env})]] : existsSync(python) ? [[python]] : process.platform === 'win32' ? [['python'], ['py', '-3.14'], ['py', '-3']] : [['python3.14'], ['python3'], ['python']]
  let base
  for (const [command, ...prefix] of candidates) {
    try {await run(command, [...prefix, '-c', 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 14) else 1)'], {cwd:root, env, capture:true}); base = [command, ...prefix]; break} catch {}
  }
  if (!base) throw Error(copy('desktop.setup.pythonMissing'))
  // Resolve launchers such as `py -3` to the interpreter that will own the lock.
  const lockPython = (await run(base[0], [...base.slice(1), '-c', 'import sys; print(sys.executable)'], {cwd:root, env, capture:true})).trim()
  const lock = await acquireSetupLock(lockPython, path.join(runtime, 'desktop-prepare.lock'), {env, copy})
  try {
    const stateFile = path.join(runtime, 'desktop-setup.json')
    let cache = {}
    try {cache = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(await readFile(stateFile)))}
    catch (error) {
      if (error.code !== 'ENOENT') await invalidState(error)
    }
    async function invalidState(cause) {
      const error = new Error(copy('desktop.setup.invalidState', {path: stateFile}), {cause})
      error.code = 'desktop/setup-state-invalid'
      throw error
    }
    const fields = new Set(['python', 'web', 'desktop', 'model-runtime', 'catalogs', 'build', 'buildArtifacts'])
    if (!cache || typeof cache !== 'object' || Array.isArray(cache) || Object.entries(cache).some(
      ([key, value]) => !fields.has(key) || typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value))) {
      await invalidState()
    }
    async function save() {
      const temporary = stateFile + '.' + randomUUID() + '.tmp'
      await writeFile(temporary, JSON.stringify(cache, null, 2) + '\n')
      await rename(temporary, stateFile)
    }
    if (!existsSync(python)) {
      notify(copy('desktop.setup.preparingPython'))
      await run(base[0], [...base.slice(1), '-m', 'venv', path.dirname(path.dirname(python))], {cwd:root, env})
    }
    const pythonHash = await fingerprint(root, ['pyproject.toml', 'build_support.py', 'MANIFEST.in', 'README.md'])
    let pythonReady = false
    try {await run(python, ['-c', 'import inspect; import mcp; import uvicorn; from playwright.async_api import BrowserType; assert "no_defaults" in inspect.signature(BrowserType.connect_over_cdp).parameters'], {cwd:root, env, capture:true}); pythonReady = true} catch {}
    if (!pythonReady || cache.python !== pythonHash) {
      notify(copy('desktop.setup.preparingDependencies'))
      await run(python, ['-m', 'pip', 'install', '-e', '.[browser]'], {cwd:root, env})
      cache.python = pythonHash; await save()
    }
    for (const project of ['web', 'desktop', 'model-runtime']) {
      const hash = await fingerprint(root, [`${project}/package.json`, `${project}/package-lock.json`])
      if (cache[project] !== hash || !existsSync(path.join(root, project, 'node_modules', project === 'web' ? 'typescript/package.json' : project === 'desktop' ? 'electron/package.json' : '@earendil-works/pi-ai/package.json'))) {
        notify(copy('desktop.setup.projectDependencies', {project}))
        await run(process.execPath, [npm, 'ci', '--include=dev', '--no-fund', '--no-audit'], {cwd:path.join(root, project), env})
        cache[project] = hash; await save()
      }
    }
    notify(copy('desktop.setup.checkingElectron'))
    try {await run(process.execPath, ['node_modules/electron/install.js'], {cwd:path.join(root, 'desktop'), env})} catch (error) {throw new Error(copy('desktop.setup.electronFailed', {error: error.message}), {cause: error})}
    await run(process.execPath, ['-e', 'const fs=require("node:fs");const binary=require("electron");if(!fs.existsSync(binary))process.exit(1)'], {cwd:path.join(root, 'desktop'), env, capture:true})
    const catalogHash = await fingerprint(root, ['web/src', 'harness/i18n/locales', 'scripts/generate_i18n.py', 'scripts/check_i18n.mjs'])
    if (cache.catalogs !== catalogHash) {
      notify(copy('desktop.setup.checkingResources'))
      await run(python, ['scripts/generate_i18n.py', '--check'], {cwd:root, env:{...env, PYTHONUTF8:'1'}, capture:true})
      await run(process.execPath, ['scripts/check_i18n.mjs'], {cwd:root, env, capture:true})
      cache.catalogs = catalogHash; await save()
    }
    const buildHash = await fingerprint(root, ['model-runtime/build.mjs', 'model-runtime/bridge.mjs', 'model-runtime/deepseek.mjs', 'model-runtime/provider-metadata.json', 'model-runtime/package.json', 'model-runtime/package-lock.json', 'harness/llm/runtime', 'web/src', 'web/public', 'web/index.html', 'web/package.json', 'web/package-lock.json', 'web/vite.config.ts', 'web/tsconfig.json', 'web/tsconfig.app.json', 'web/tsconfig.node.json', 'web/tailwind.config.js', 'web/postcss.config.js', 'harness/i18n/locales', 'scripts/check_i18n.mjs', 'scripts/check_ui_rules.mjs', 'scripts/ui_css_rules.mjs', 'scripts/ui_class_rules.mjs', 'scripts/generate_i18n.py', 'scripts/publish_web_build.mjs', 'desktop/build-artifacts.mjs'])
    const artifacts = await buildArtifacts(path.join(root, 'web/dist'))
    if (cache.build !== buildHash || !artifacts || cache.buildArtifacts !== artifacts) {
      notify(copy('desktop.setup.building'))
      await run(process.execPath, [npm, 'run', 'build'], {cwd:path.join(root, 'web'), env})
      const built = await buildArtifacts(path.join(root, 'web/dist'))
      if (!built) throw new Error(copy('desktop.setup.invalidBuild'))
      cache.build = buildHash; cache.buildArtifacts = built; await save()
    }
    notify(copy('desktop.setup.ready'))
    return {python, env:{...env, HARNESS_PYTHON:python}}
  } finally {
    await lock.release()
  }
}
