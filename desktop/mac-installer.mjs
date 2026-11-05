import {createHash} from 'node:crypto'
import {mkdtemp, open, writeFile, mkdir, access, lstat, rm} from 'node:fs/promises'
import {constants} from 'node:fs'
import {execFile, spawn} from 'node:child_process'
import {promisify} from 'node:util'
import path from 'node:path'

const execute = promisify(execFile)
const run = (file, args) => execute(file, args, {timeout:120000, maxBuffer:1024 * 1024})
export class InstallerFailure extends Error {
  constructor(menuKey, cause, params = {}) {super(menuKey, {cause}); this.menuKey = menuKey; this.params = params}
}

/** 传输只写入本次独占目录，完整大小和 GitHub SHA-256 均确认后才交付。 */
export async function downloadInstaller({fetch, asset, directory, signal, progress, idleTimeoutMs = 30000}) {
  if (!/^sha256:[a-f0-9]{64}$/.test(asset.digest || '')) throw new InstallerFailure('updateIntegrityFailed')
  const idle = new AbortController()
  const downloadSignal = signal ? AbortSignal.any([signal, idle.signal]) : idle.signal
  let timer, file
  const active = () => {
    clearTimeout(timer)
    timer = setTimeout(() => idle.abort(Object.assign(Error('UPDATE_DOWNLOAD_IDLE_TIMEOUT'), {name:'TimeoutError'})), idleTimeoutMs)
  }
  progress(null, {phase:'connecting', received:0, total:asset.size})
  active()
  try {
    downloadSignal.throwIfAborted()
    const response = await fetch(asset.browser_download_url, {signal:downloadSignal, redirect:'follow'})
    if (!response.ok) {await response.body?.cancel(); throw new InstallerFailure('updateHttpFailure', Error(String(response.status)), {status:response.status})}
    if (!response.body) throw new InstallerFailure('updateIntegrityFailed')
    const filename = path.join(directory, 'installer.dmg')
    file = await open(filename, 'wx', 0o600)
    let size = 0
    const hash = createHash('sha256')
    for await (const chunk of response.body) {
      downloadSignal.throwIfAborted()
      if (!chunk.byteLength) continue
      active()
      size += chunk.byteLength
      if (size > asset.size) throw new InstallerFailure('updateIntegrityFailed')
      hash.update(chunk)
      await file.writeFile(chunk)
      downloadSignal.throwIfAborted()
      progress(Math.min(99.9, size * 100 / asset.size), {phase:'downloading', received:size, total:asset.size})
    }
    downloadSignal.throwIfAborted()
    clearTimeout(timer)
    progress(100, {phase:'verifying', received:size, total:asset.size})
    if (size !== asset.size || `sha256:${hash.digest('hex')}` !== asset.digest) throw new InstallerFailure('updateIntegrityFailed')
    await file.sync()
    return filename
  } catch (error) {
    if (downloadSignal.aborted) throw downloadSignal.reason
    throw error
  } finally {clearTimeout(timer); await file?.close()}
}

/** 脚本在主进程实际退出后替换同目录应用；旧包保留为备份，不触及用户资料。 */
export const installScript = `#!/bin/bash
set -eu
pid="$1"; target="$2"; staged="$3"; backup="$4"; workspace="$5"; failure="$6"; identity="$7"; requirement="$8"
exec >>"$workspace/install.log" 2>&1
fail() {
  printf '%s\\n' "$1" >&2
  /usr/bin/osascript -e 'on run argv' -e 'display alert "Harness" message (item 1 of argv) as critical' -e 'end run' "$failure" || true
  exit 1
}
for ((i=0; i<120; i++)); do
  if ! kill -0 "$pid" 2>/dev/null; then break; fi
  /bin/sleep 1
done
if kill -0 "$pid" 2>/dev/null; then fail parent-still-running; fi
if [ ! -d "$target" ] || [ -L "$target" ] || [ ! -d "$staged" ] || [ -e "$backup" ]; then fail application-path-changed; fi
if [ "$(/usr/bin/stat -f '%d:%i' "$target")" != "$identity" ]; then fail target-identity-changed; fi
if [ -n "$requirement" ]; then
  if ! /usr/bin/codesign --verify --deep --strict -R "$requirement" "$staged"; then fail signature-invalid; fi
else
  if ! /usr/bin/codesign --verify --deep --strict "$staged"; then fail signature-invalid; fi
fi
if ! /bin/mv "$target" "$backup"; then fail backup-failed; fi
if ! /bin/mv "$staged" "$target"; then
  /bin/mv "$backup" "$target" || fail rollback-failed
  fail replacement-failed
fi
if ! /usr/bin/open "$target"; then fail relaunch-uncertain; fi
printf 'installed\\n' > "$workspace/result"
`

export class MacInstaller {
  constructor({appPath, cachePath, fetch = globalThis.fetch, command = run, launch = spawn, arch = process.arch}) {
    this.arch = arch
    this.appPath = appPath
    this.cachePath = cachePath
    this.fetch = fetch
    this.command = command
    this.launch = launch
    this.controller = null
    this.cancelled = false
    this.prepared = null
    this.handoff = null
  }
  async prepare(asset, version, progress) {
    const target = this.appPath
    const identity = await lstat(target)
    if (!target.endsWith('.app') || identity.isSymbolicLink() || target.startsWith('/Volumes/') || target.includes('/AppTranslocation/')) throw new InstallerFailure('updateInstallLocation')
    try {await access(path.dirname(target), constants.W_OK)} catch (cause) {throw new InstallerFailure('updateInstallLocation', cause)}
    await mkdir(this.cachePath, {recursive:true})
    const workspace = await mkdtemp(path.join(this.cachePath, 'mac-update-'))
    this.controller = new AbortController()
    if (this.cancelled) this.controller.abort()
    const signal = AbortSignal.any([this.controller.signal, AbortSignal.timeout(30 * 60 * 1000)])
    let staging
    try {
      const image = await downloadInstaller({fetch:this.fetch, asset, directory:workspace, signal, progress})
      const mount = path.join(workspace, 'volume')
      await mkdir(mount)
      // 固定挂载目录，不执行镜像内程序，也不允许自动打开 Finder。
      await this.command('/usr/bin/hdiutil', ['attach', '-readonly', '-nobrowse', '-mountpoint', mount, image])
      let preparationError
      try {
        const source = path.join(mount, 'Harness.app')
        if (!(await lstat(source)).isDirectory()) throw new InstallerFailure('updateInvalidRelease')
        const plist = path.join(source, 'Contents/Info.plist')
        const value = async (file, key) => (await this.command('/usr/libexec/PlistBuddy', ['-c', `Print :${key}`, file])).stdout.trim()
        const currentPlist = path.join(target, 'Contents/Info.plist')
        if (await value(plist, 'CFBundleIdentifier') !== await value(currentPlist, 'CFBundleIdentifier') || await value(plist, 'CFBundleShortVersionString') !== version) throw new InstallerFailure('updateInvalidRelease')
        // 保持当前安装包的签名要求；不通过关闭签名校验来安装更新。
        let signature
        try {signature = await this.command('/usr/bin/codesign', ['-d', '-r-', '--verbose=4', target])}
        catch (cause) {throw new InstallerFailure('updateCurrentSignatureInvalid', cause)}
        const requirement = (signature.stderr + signature.stdout).split('\n').find(line => line.startsWith('designated => '))?.slice(14)
        const adhoc = (signature.stderr + signature.stdout).includes('Signature=adhoc')
        if (!requirement && !adhoc) throw new InstallerFailure('updateIntegrityFailed')
        // 无资源封套的 linker 签名不是有效应用签名；只允许替换为已验证的新包，绝不修签旧包。
        let unsealedLinker = false
        if (adhoc && (signature.stderr + signature.stdout).includes('linker-signed') && (signature.stderr + signature.stdout).includes('Sealed Resources=none') && (signature.stderr + signature.stdout).includes('Info.plist=not bound')) {
          try {await lstat(path.join(target, 'Contents/_CodeSignature/CodeResources'))}
          catch (error) {if (error.code === 'ENOENT') unsealedLinker = true; else throw error}
        }
        if (!unsealedLinker) {
          try {await this.command('/usr/bin/codesign', ['--verify', '--deep', '--strict', target])}
          catch (cause) {throw new InstallerFailure('updateCurrentSignatureInvalid', cause)}
        }
        const signatureArgs = adhoc ? [] : ['-R', requirement]
        try {
          await access(path.join(source, 'Contents/_CodeSignature/CodeResources'))
          await this.command('/usr/bin/codesign', ['--verify', '--deep', '--strict', ...signatureArgs, source])
        } catch (cause) {throw new InstallerFailure('updateIntegrityFailed', cause)}
        const executable = await value(plist, 'CFBundleExecutable')
        if (!/^[A-Za-z0-9._-]+$/.test(executable)) throw new InstallerFailure('updateInvalidRelease')
        await this.command('/usr/bin/lipo', [path.join(source, 'Contents/MacOS', executable), '-verify_arch', this.arch === 'arm64' ? 'arm64' : 'x86_64'])
        progress(100, {phase:'preparing', received:asset.size, total:asset.size})
        staging = await mkdtemp(path.join(path.dirname(target), '.harness-update-'))
        const staged = path.join(staging, 'Harness.app')
        await this.command('/usr/bin/ditto', [source, staged])
        try {await this.command('/usr/bin/codesign', ['--verify', '--deep', '--strict', ...signatureArgs, staged])}
        catch (cause) {throw new InstallerFailure('updateIntegrityFailed', cause)}
        const backup = path.join(staging, 'previous.app')
        const script = path.join(workspace, 'install.sh')
        await writeFile(script, installScript, {mode:0o700, flag:'wx'})
        this.prepared = {workspace, script, target, staged, backup, identity:`${identity.dev}:${identity.ino}`, requirement:adhoc ? '' : requirement}
      } catch (error) {preparationError = error; throw error}
      finally {
        try {await this.command('/usr/bin/hdiutil', ['detach', mount])}
        catch (error) {
          if (preparationError) throw new AggregateError([preparationError, error], `${preparationError.message}\n${error.message}`)
          throw error
        }
      }
      // 准备阶段的取消不能发布可安装状态。
      signal.throwIfAborted()
      await rm(image)
      return this.prepared
    } catch (error) {
      this.prepared = null
      // 仅清理本次拥有的暂存；诊断目录保留。
      if (staging) {
        try {await rm(staging, {recursive:true, force:true})}
        catch (cleanupError) {throw new AggregateError([error, cleanupError], `${error.message}\n${cleanupError.message}`)}
      }
      throw error
    } finally {this.controller = null}
  }
  cancel() {this.cancelled = true; this.controller?.abort()}
  async discard() {
    if (this.handoff || !this.prepared) return
    await rm(path.dirname(this.prepared.staged), {recursive:true, force:true})
    await rm(this.prepared.workspace, {recursive:true, force:true})
    this.prepared = null
  }
  install(failureText) {
    if (this.handoff) return this.handoff
    if (!this.prepared) return Promise.reject(new InstallerFailure('updateInvalidRelease'))
    const {workspace, script, target, staged, backup, identity, requirement} = this.prepared
    this.handoff = new Promise((resolve, reject) => {
      const child = this.launch('/bin/bash', [script, String(process.pid), target, staged, backup, workspace, `${failureText}\n${workspace}/install.log`, identity, requirement], {detached:true, stdio:'ignore'})
      child.once('error', reject)
      child.once('spawn', () => {child.unref(); resolve()})
    })
    return this.handoff
  }
}
