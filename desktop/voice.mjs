import {randomUUID} from 'node:crypto'
import {execFile} from 'node:child_process'
import {promisify} from 'node:util'
import {open, mkdir, rename, unlink} from 'node:fs/promises'
import path from 'node:path'

const execute = promisify(execFile)
const DEFAULT_SHORTCUT = 'Control+Alt+Space'
class VoiceError extends Error {
  constructor(key, cause) {super(key, {cause}); this.copy = {key:'ui.voice.global.' + key}}
}

/** 自有错误只传资源身份；技术详情只保留实际底层诊断。 */
function voiceFailure(error) {
  let cause = error.cause ?? error
  while (cause.copy && cause.cause) cause = cause.cause
  const diagnostic = cause.copy ? undefined : cause.message
  return {copy:error.copy ?? {key:'ui.voice.global.failed'}, ...(diagnostic ? {diagnostic} : {})}
}

/** 全局听写仅由 Harness 的主进程、录音窗口和所属操作共同拥有。 */
export class DesktopVoice {
  constructor({app, BrowserWindow, globalShortcut, clipboard, screen, systemPreferences, shell, systemPermissions, getWindow, getURL, preload, nativePath, platform = process.platform, fetchCapabilities = fetch}) {
    Object.assign(this, {app, BrowserWindow, globalShortcut, clipboard, screen, systemPreferences, shell, systemPermissions, getWindow, getURL, preload, nativePath, platform})
    this.preferences = {version:1, enabled:false, shortcut:DEFAULT_SHORTCUT}
    this.file = path.join(app.getPath('userData'), 'voice.json')
    this.window = null
    this.active = null
    this.registered = null
    this.closed = false
    this.pending = null
    this.insertion = null
    this.failure = null
    this.original = null
    this.captureFailure = null
    this.context = null
    this.receipt = null
    this.supported = ['darwin','win32'].includes(platform)
    this.fetchCapabilities = fetchCapabilities
  }
  trusted(event, window) {
    return Boolean(window && !window.isDestroyed() && event.sender === window.webContents && event.senderFrame === window.webContents.mainFrame && new URL(event.senderFrame.url).origin === new URL(this.getURL()).origin)
  }
  permission() {return this.platform !== 'darwin' || this.systemPreferences.isTrustedAccessibilityClient(false)}
  setContext(value) {
    if (!value || typeof value.id !== 'string' || value.id.length > 100 || !(value.sessionId === null || typeof value.sessionId === 'string') || !Number.isSafeInteger(value.revision) || value.revision < 0 || !Number.isSafeInteger(value.generation) || value.generation < 0 || typeof value.composing !== 'boolean' || typeof value.editor !== 'boolean') throw Error('Invalid voice editor context')
    if (value.editor || this.context?.id === value.id) this.context = {id:value.id, sessionId:value.sessionId, revision:value.revision, generation:value.generation, composing:value.composing, editor:value.editor}
  }
  receive(value) {
    if (!value || typeof value.id !== 'string' || typeof value.accepted !== 'boolean') throw Error('Invalid voice insertion receipt')
    if (this.receipt?.id === value.id) this.receipt.resolve(value.accepted ? 'draft' : 'copied')
  }
  async insertDraft(current, transcript) {
    if (!current.context?.editor || JSON.stringify(current.context) !== JSON.stringify(this.context) || !this.getWindow()?.isFocused()) return 'copied'
    return await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new VoiceError('pasteUnconfirmed')), 8000)
      this.receipt = {id:current.id, resolve, reject, cleanup:() => clearTimeout(timer)}
      this.getWindow().webContents.send('voice-insert', {id:current.id, context:current.context, transcript})
    }).finally(() => {this.receipt?.cleanup(); this.receipt = null})
  }
  snapshot() {
    return {supported:this.supported, enabled:Boolean(this.registered), shortcut:this.preferences.shortcut,
      accessibility:this.permission(), phase:this.active?.phase ?? (this.insertion ? 'finishing' : 'idle'), error:this.failure}
  }
  validate(value) {
    if (!value || Object.keys(value).sort().join(',') !== 'enabled,shortcut,version' || value.version !== 1 || typeof value.enabled !== 'boolean'
        || typeof value.shortcut !== 'string' || value.shortcut.length > 100 || !/^(?:(?:Control|Command|CommandOrControl|Alt|Option|Shift)\+)+[A-Za-z0-9]+$/.test(value.shortcut)) throw new VoiceError('settingsInvalid')
    return value
  }
  async initialize() {
    if (this.platform === 'win32') {
      this.supported = false
      try {
        const response = await this.fetchCapabilities(new URL('/api/v1/voice/dictation', this.getURL()),
          {signal:AbortSignal.timeout(5000), redirect:'error'})
        if (!response.ok) throw new VoiceError('cpuCheckFailed')
        const capabilities = await response.json()
        if (typeof capabilities.cpu_supported !== 'boolean') throw new VoiceError('cpuCheckFailed')
        if (!capabilities.cpu_supported) {
          this.failure = capabilities.reason?.key === 'ui.voice.global.cpuUnsupported'
            ? {key:'ui.voice.global.cpuUnsupported'} : {key:'ui.voice.global.cpuCheckFailed'}
          return
        }
        this.supported = true
      } catch (error) {
        this.failure = {key:'ui.voice.global.cpuCheckFailed'}
        console.error('Voice CPU capability check failed:', error)
        return
      }
    }
    try {
      let handle
      try {handle = await open(this.file, 'r')} catch (error) {if (error.code === 'ENOENT') return; throw error}
      let bytes
      try {
        const buffer = Buffer.alloc(16385)
        let count = 0, read
        do {read = await handle.read(buffer, count, buffer.length - count, null); count += read.bytesRead} while (read.bytesRead && count < buffer.length)
        if (count > 16384) throw new VoiceError('settingsInvalid')
        bytes = buffer.subarray(0, count)
      } finally {await handle.close()}
      this.preferences = this.validate(JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(bytes)))
      this.original = bytes
      if (this.preferences.enabled) this.register(this.preferences.shortcut)
    } catch (error) {this.failure = error.copy ?? {key:'ui.voice.global.settingsInvalid'}; console.error('Voice settings failed:', error)}
  }
  register(shortcut) {
    if (!this.supported) throw new VoiceError(this.failure?.key === 'ui.voice.global.cpuUnsupported' ? 'cpuUnsupported' : 'unsupported')
    if (this.closed) throw new VoiceError('unsupported')
    try {if (!this.globalShortcut.register(shortcut, () => {void this.toggle().catch(error => this.fail(error))})) throw new VoiceError('shortcutUnavailable')}
    catch (cause) {throw cause instanceof VoiceError ? cause : new VoiceError('shortcutUnavailable', cause)}
    this.registered = shortcut
  }
  configure(value) {
    if (this.pending) return Promise.reject(new VoiceError('busy'))
    this.pending = this.save(value).finally(() => {this.pending = null})
    return this.pending
  }
  async save(value) {
    if (!this.supported) throw new VoiceError(this.failure?.key === 'ui.voice.global.cpuUnsupported' ? 'cpuUnsupported' : 'unsupported')
    let next
    try {next = this.validate({version:1, ...value})} catch (cause) {throw new VoiceError('shortcutInvalid', cause)}
    if (this.closed || this.active || this.insertion) throw new VoiceError('busy')
    if (this.failure?.key === 'ui.voice.global.settingsInvalid') throw new VoiceError('settingsInvalid')
    const old = this.registered
    let added = false
    if (next.enabled && old !== next.shortcut) {this.register(next.shortcut); added = true}
    const temporary = this.file + '.' + randomUUID() + '.tmp'
    let handle, renameAttempted = false
    try {
      await this.checkOriginal()
      await mkdir(path.dirname(this.file), {recursive:true})
      handle = await open(temporary, 'wx', 0o600)
      const bytes = Buffer.from(JSON.stringify(next) + '\n')
      await handle.writeFile(bytes)
      await handle.sync()
      await handle.close(); handle = null
      await this.checkOriginal()
      renameAttempted = true
      await rename(temporary, this.file)
      this.original = bytes
    } catch (error) {
      if (added) {this.globalShortcut.unregister(next.shortcut); this.registered = old}
      try {await handle?.close()} finally {try {await unlink(temporary)} catch (cleanup) {if (cleanup.code !== 'ENOENT') console.error('Voice temporary cleanup failed:', cleanup)}}
      if (renameAttempted) {this.failure = {key:'ui.voice.global.saveUnconfirmed'}; throw new VoiceError('saveUnconfirmed', error)}
      throw error instanceof VoiceError ? error : new VoiceError('saveFailed', error)
    }
    if (old && (!next.enabled || old !== next.shortcut)) this.globalShortcut.unregister(old)
    this.preferences = next
    this.registered = next.enabled ? next.shortcut : null
    this.failure = null
    return this.snapshot()
  }
  async checkOriginal() {
    let handle
    try {handle = await open(this.file, 'r')} catch (error) {if (error.code === 'ENOENT' && this.original === null) return; throw error}
    try {
      const bytes = Buffer.alloc(16385)
      let count = 0, read
      do {read = await handle.read(bytes, count, bytes.length - count, null); count += read.bytesRead} while (read.bytesRead && count < bytes.length)
      if (this.original === null || !this.original.equals(bytes.subarray(0, count))) throw new VoiceError('settingsChanged')
    } finally {await handle.close()}
  }
  async native(action, target) {
    if (!['target','paste'].includes(action) || target !== undefined && !/^[1-9]\d{0,19}$/.test(target)) throw new VoiceError('pasteUnconfirmed')
    const swift = this.platform === 'darwin' && this.nativePath.endsWith('.swift')
    const command = this.platform === 'win32' ? path.join(process.env.SystemRoot || 'C:\\Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe') : swift ? '/usr/bin/xcrun' : this.nativePath
    const args = this.platform === 'win32'
      ? ['-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',this.nativePath,'-Action',action,...(target ? ['-Target',target] : [])]
      : [...(swift ? ['swift',this.nativePath] : []),action,...(target ? [target] : [])]
    const {stdout} = await execute(command, args, {timeout:8000, maxBuffer:8192, windowsHide:true})
    const result = stdout.trim()
    if (action === 'target' ? !/^[1-9]\d{0,19}$/.test(result) : !['sent','copied'].includes(result)) throw new VoiceError('pasteUnconfirmed')
    return result
  }
  ensureWindow() {
    if (this.window && !this.window.isDestroyed()) return this.window
    const area = this.screen.getDisplayNearestPoint(this.screen.getCursorScreenPoint()).workArea
    const window = new this.BrowserWindow({width:440, height:280, x:Math.round(area.x + (area.width - 440) / 2), y:area.y + area.height - 300,
      show:false, frame:false, resizable:false, minimizable:false, maximizable:false, focusable:false, alwaysOnTop:true, skipTaskbar:true,
      webPreferences:{preload:this.preload, contextIsolation:true, nodeIntegration:false, sandbox:true, autoplayPolicy:'no-user-gesture-required'}})
    this.window = window
    window.setMenu(null)
    window.webContents.setWindowOpenHandler(() => ({action:'deny'}))
    window.webContents.on('will-navigate', (event, url) => {if (url !== new URL('/voice.html', this.getURL()).href) event.preventDefault()})
    window.webContents.on('render-process-gone', () => this.cancel())
    let loaded = false
    window.webContents.on('did-start-navigation', (_event, _url, inPlace, mainFrame) => {if (loaded && mainFrame && !inPlace) this.cancel()})
    window.on('closed', () => {if (this.window === window) {this.window = null; if (!this.insertion) this.active = null}})
    this.loading = window.loadURL(new URL('/voice.html', this.getURL()).href).then(() => {loaded = true})
    return window
  }
  async toggle() {
    if (!this.supported || this.closed || !this.registered || this.insertion) return
    if (this.getWindow()?.isFocused() && this.context?.composing) return
    if (this.active) {
      if (this.active.phase === 'recording') {this.active.phase = 'processing'; this.window.webContents.send('voice-action', {action:'stop', id:this.active.id})}
      return
    }
    const current = {id:randomUUID(), phase:'opening', target:null, own:this.getWindow()?.isFocused(), context:this.context && {...this.context}}
    this.captureFailure = null
    this.active = current
    try {
      if (!current.own && this.systemPermissions) {
        const permission = await this.systemPermissions.request('accessibility')
        if (this.closed || this.active !== current) return
        if (!permission.granted) {
          const error = new Error(permission.copy.key); error.copy = permission.copy
          throw error
        }
      }
      current.target = await this.native('target')
      if (this.closed || this.active !== current) return
      const window = this.ensureWindow()
      await this.loading
      if (this.closed || this.active !== current) return
      window.showInactive()
      // ready 回执与复用窗口共用代次；渲染器按 id 只创建一次录音。
      window.webContents.send('voice-action', {action:'start', id:current.id})
    } catch (error) {if (this.active === current) {this.active = null; throw error}}
  }
  ready() {return this.active?.phase === 'opening' ? {action:'start', id:this.active.id} : this.captureFailure}
  phase(id, phase) {
    if (this.active?.id !== id || !['recording','processing','done'].includes(phase)) return
    if (phase === 'done' && this.active.phase !== 'finishing') {this.active = null; return}
    if (this.active.phase === 'opening' || this.active.phase === 'recording') this.active.phase = phase
  }
  complete(id, transcript) {
    const current = this.active
    if (current?.id === id && current.phase === 'finishing' && this.insertion) return this.insertion
    if (this.closed || current?.id !== id || current.phase !== 'processing') return Promise.reject(new VoiceError('inactive'))
    if (this.insertion) return Promise.reject(new VoiceError('busy'))
    if (typeof transcript !== 'string' || !transcript.trim() || transcript.length > 16000) return Promise.reject(new VoiceError('empty'))
    current.phase = 'finishing'
    this.insertion = Promise.resolve().then(async () => {
      if (this.closed || this.active !== current) throw new VoiceError('inactive')
      this.clipboard.writeText(transcript)
      if (current.own) return {result:await this.insertDraft(current, transcript)}
      if (!this.permission()) return {result:'copied'}
      try {return {result:await this.native('paste', current.target)}}
      catch (error) {throw new VoiceError('pasteUnconfirmed', error)}
    }).finally(() => {if (this.active === current) this.active = null; this.insertion = null})
    return this.insertion
  }
  cancel(id) {
    if (this.active && id && this.active.id !== id || this.active?.phase === 'finishing') return
    this.active = null
    if (this.window && !this.window.isDestroyed()) {this.window.webContents.send('voice-action', {action:'cancel'}); this.window.hide()}
  }
  fail(error) {
    this.captureFailure = {action:'error', ...voiceFailure(error)}
    if (!this.closed) {
      try {
        const window = this.ensureWindow()
        void this.loading.then(() => {if (!this.closed && this.window === window) {window.showInactive(); window.webContents.send('voice-action', this.captureFailure)}}).catch(failure => console.error('Voice feedback failed:', failure))
      } catch (failure) {console.error('Voice feedback failed:', failure)}
    }
    console.error('Global dictation failed:', error)
  }
  async requestPermission() {
    if (this.systemPermissions) await this.systemPermissions.request('accessibility')
    else if (this.platform === 'darwin') {
      this.systemPreferences.isTrustedAccessibilityClient(true)
      await this.shell.openExternal('x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility')
    }
    return this.snapshot()
  }
  async close() {
    this.closed = true
    this.receipt?.reject(new VoiceError('inactive'))
    try {
      const results = await Promise.allSettled([this.pending, this.insertion])
      const errors = results.filter(result => result.status === 'rejected').map(result => result.reason)
      if (errors.length === 1) throw errors[0]
      if (errors.length) throw new AggregateError(errors, 'Voice cleanup failed')
    }
    finally {
      if (this.registered) this.globalShortcut.unregister(this.registered)
      this.registered = null
      this.active = null; this.window?.destroy(); this.window = null
    }
  }
}

export async function voiceReply(execute) {
  try {return {ok:true, data:await execute()}}
  catch (error) {return {ok:false, ...voiceFailure(error)}}
}
