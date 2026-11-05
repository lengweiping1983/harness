import {existsSync, statSync} from 'node:fs'
import {cp, mkdir, readdir, readFile, readlink, symlink, unlink, writeFile} from 'node:fs/promises'
import {fileURLToPath, pathToFileURL} from 'node:url'
import path from 'node:path'
import {runCommand} from './setup.mjs'
import {setupCopy} from './setup-copy.mjs'
import {prepareVoice, stageVoiceOverride} from './prepare-voice.mjs'

// uv 托管的 CPython 用绝对链接指向安装目录；原样复制到暂存后，链接仍指向应用包外的
// 构建目录。codesign --strict 把这种链接判为非法目标并拒绝封签，因此按源目录换算成
// 包内相对链接；指向源目录外的链接无法安全映射，直接拒绝打包。
export async function relativizeStagedLinks(stagedRoot, sourceRoot, {copy = setupCopy()} = {}) {
  const walk = async directory => {
    for (const entry of await readdir(directory, {withFileTypes:true})) {
      const entryPath = path.join(directory, entry.name)
      if (entry.isSymbolicLink()) {
        const target = await readlink(entryPath)
        if (!path.isAbsolute(target)) continue
        const relative = path.relative(sourceRoot, target)
        if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
          throw Error(copy('desktop.release.linkOutside', {path:entryPath}))
        }
        const mapped = path.join(stagedRoot, relative)
        await unlink(entryPath)
        await symlink(path.relative(path.dirname(entryPath), mapped), entryPath)
      } else if (entry.isDirectory()) {
        await walk(entryPath)
      }
    }
  }
  await walk(stagedRoot)
}

// 导入时不安装或写入；命令行与隔离回归调用同一准备流程。
export async function prepareRelease({root = fileURLToPath(new URL('../', import.meta.url)),
  platform = process.platform, arch = process.arch, language = process.env.HARNESS_LANGUAGE,
  execute = runCommand, log = console.log, voiceBinary = process.env.HARNESS_HEX_BINARY} = {}) {
  const copy = setupCopy(language)
  const run = (command, args, options) => execute(command, args, {...options, language})
  const build = path.join(root, '.run', 'desktop-release')
  const runtime = path.join(build, 'managed')
  const staging = path.join(build, 'resources')
  if (!['darwin','win32'].includes(platform)) throw Error(copy('desktop.release.platformUnsupported'))
  // 暂存目录必须每次全新：cp 只做增量覆盖，不会清理上次构建残留的 web 资源与 site。
  // 复用旧目录会让上一次构建的前端产物混进新安装包（实测多出约 4MB 无效资源），
  // 与下方 managed Python 的「fresh build directory」要求同源，这里前置断言而非静默带病打包。
  if (existsSync(path.join(staging, 'site')) || existsSync(path.join(staging, 'web'))) {
    throw Error(copy('desktop.release.stagingNotFresh', {path:staging}))
  }
  await mkdir(staging, {recursive:true})
  const voice = await prepareVoice({root, staging, platform, arch, execute:run, copy, binary:voiceBinary})
  // 固定下载清单及哈希，避免 uv 内置目录过旧时找不到所需补丁版本。
  // 在 Electron 下载前完成 Python 引导；镜像仍由 uv 的标准环境变量选择。
  const downloads = pathToFileURL(path.join(root, 'desktop', 'python-downloads.json')).href
  try {
    await run('uv', ['python', 'install', '3.14.10', '--install-dir', runtime, '--no-bin', '--no-registry', '--python-downloads-json-url', downloads], {cwd:root})
  } catch (error) {
    log(copy('desktop.release.pythonInstallHelp'))
    throw error
  }
  const distributions = (await readdir(runtime)).filter(name => name.startsWith('cpython-3.14.10-'))
  if (distributions.length !== 1) throw Error(copy('desktop.release.runtimeCount'))
  const pythonRoot = path.join(runtime, distributions[0])
  const python = path.join(pythonRoot, platform === 'win32' ? 'python.exe' : 'bin/python3')
  const identity = JSON.parse(await run(python, ['-c', 'import json, platform, sys; print(json.dumps({"platform":sys.platform, "machine":platform.machine(), "version":platform.python_version()}))'], {cwd:root, capture:true}))
  const pythonArch = {arm64:'arm64', aarch64:'arm64', AMD64:'x64', x86_64:'x64'}[identity.machine]
  if (identity.platform !== platform || pythonArch !== arch || identity.version !== '3.14.10') throw Error(copy('desktop.release.runtimeMismatch'))
  await run(process.execPath, [path.join(root, 'desktop/node_modules/electron/install.js')], {cwd:path.join(root, 'desktop')})
  // electron 的 install.js 仅凭 path.txt 判断是否已安装；path.txt 与 dist 不一致时
  // 它会静默跳过，让打包在十几分钟后才在 electron-builder 处报 electronDist 不存在。
  // 这里前置断言，把这种不一致立刻暴露在准备阶段。
  const electronDist = path.join(root, 'desktop', 'node_modules', 'electron', 'dist')
  if (!existsSync(electronDist)) throw Error(copy('desktop.release.electronMissing', {path:electronDist}))
  await run('uv', ['pip', 'install', '--python', python, '--target', path.join(staging, 'site'), '--require-hashes', '--requirements', path.join(root, 'desktop/requirements.lock')], {cwd:root})
  await run('uv', ['pip', 'install', '--python', python, '--target', path.join(staging, 'site'), '--no-deps', '.'], {cwd:root})
  if (voiceBinary) await stageVoiceOverride({root, staging, binary:voiceBinary, metadata:voice, copy})
  // PYTHONPATH 指向的目录不会被 site 处理，依赖里的 .pth（pywin32 靠它注册 win32/lib 与 DLL 目录）
  // 因此不生效，pywintypes 无法导入。让暂存 site 目录按 site 语义加载自身，保证打包运行时与构建自检一致。
  await writeFile(path.join(staging, 'site', 'sitecustomize.py'), 'import os, site\nsite.addsitedir(os.path.dirname(os.path.abspath(__file__)))\n')
  await cp(pythonRoot, path.join(staging, 'python'), {recursive:true, dereference:false})
  await relativizeStagedLinks(path.join(staging, 'python'), pythonRoot, {copy})
  await cp(path.join(root, 'web', 'dist'), path.join(staging, 'web'), {recursive:true})
  // 系统技能目录可选：当前检出允许不附带预装技能，目录缺失或非目录时跳过而不是失败。
  const systemSkills = path.join(root, '.harness', 'skills')
  if (existsSync(systemSkills) && statSync(systemSkills).isDirectory()) {
    await cp(systemSkills, path.join(staging, 'site', '.harness', 'skills'), {recursive:true})
  }
  const bundledSkills = JSON.parse(await run(python, ['-c', 'import json; from harness.skills.registry import build_default_registry; from pathlib import Path; registry = build_default_registry(cwd=str(Path.cwd()), home=str(Path.cwd() / "empty-home")); names = sorted(skill.name for skill in registry.list()); print(json.dumps(names))'], {cwd:staging, capture:true, env:{...process.env, PYTHONPATH:path.join(staging, 'site'), PYTHONNOUSERSITE:'1'}}))
  const metadata = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(await readFile(path.join(root, 'desktop', 'package.json'))))
  await writeFile(path.join(staging, 'runtime.json'), JSON.stringify({version:metadata.version, platform, arch, python:'3.14.10', bundledSkills, voice}, null, 2) + '\n')
  await run(python, ['-c', 'import harness.cli, mcp, uvicorn, PIL; from playwright.async_api import BrowserType; import inspect; assert "no_defaults" in inspect.signature(BrowserType.connect_over_cdp).parameters'], {cwd:staging, env:{...process.env, PYTHONPATH:path.join(staging, 'site'), PYTHONNOUSERSITE:'1'}})
  // 官方 3.3.0 Windows 引擎要求 AVX2（Intel Haswell / AMD Excavator 及以后）。
  // 构建机 CPU 不支持时进程启动即退出，握手必然失败；此时可设
  // HARNESS_SKIP_VOICE_SMOKE=1 跳过引擎动态冒烟——引擎身份/版本/sha256 的静态
  // 校验仍由 verify-runtime.mjs 在 beforePack 全量执行，只是不再要求构建机
  // 能实际运行该引擎（产物面向支持 AVX2 的目标机器）。
  if (!process.env.HARNESS_SKIP_VOICE_SMOKE) {
    await run(python, ['-c', 'from pathlib import Path; from tempfile import TemporaryDirectory; from harness.tools.builtin.hex_tools import HexServiceManager; root = Path.cwd();\nwith TemporaryDirectory(prefix="harness-voice-check-") as temporary:\n service = HexServiceManager(binary=root / "site" / ' + JSON.stringify(voice.path) + ', discovery=Path(temporary) / "local-api.json")\n try:\n  service.ensure_running()\n finally:\n  service.stop()'], {cwd:staging, env:{...process.env, PYTHONPATH:path.join(staging, 'site'), PYTHONNOUSERSITE:'1'}})
  }
  log(copy('desktop.release.staged', {path:staging}))
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await prepareRelease()
