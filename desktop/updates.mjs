export class DesktopUpdates {
  constructor({updater, nativeUpdater = null, packaged, notify = () => {}}) {
    this.updater = updater
    this.nativeUpdater = nativeUpdater
    this.packaged = packaged
    this.notify = notify
    this.state = packaged ? 'idle' : 'development'
    this.pending = null
    this.operation = null
    this.version = null
    this.progress = null
    this.error = null
    updater.autoDownload = false
    updater.autoInstallOnAppQuit = true
    updater.allowPrerelease = false
    updater.allowDowngrade = false
    updater.on('update-available', info => {
      if (this.operation === 'check') this.available = info
    })
    updater.on('update-not-available', () => {
      if (this.operation === 'check') this.available = false
    })
    updater.on('download-progress', info => {
      if (this.operation !== 'download' || !Number.isFinite(info.percent)) return
      const progress = Math.floor(Math.max(0, Math.min(100, info.percent)))
      if (this.progress !== progress) {this.progress = progress; this.notify(this.state)}
    })
    // 下载事件和文件传输 Promise 都可能早于 macOS 原生签名校验。
    updater.on('error', error => {
      this.error = error
      if (!this.pending) this.setState('error')
    })
  }
  snapshot() {return {state:this.state, version:this.version, progress:this.progress}}
  setState(state) {this.state = state; this.notify(state)}
  run(operation, execute) {
    this.operation = operation
    this.error = null
    // 先登记在途任务，再调用第三方入口，覆盖同步事件与同步抛错。
    this.pending = Promise.resolve().then(execute).catch(error => {
      this.error = error
      this.setState('error')
      throw error
    }).finally(() => {this.pending = null; this.operation = null})
    this.setState(operation === 'check' ? 'checking' : 'downloading')
    return this.pending
  }
  check() {
    if (!this.packaged) return Promise.resolve(this.snapshot())
    if (this.pending) return this.pending
    if (this.state === 'downloaded') return Promise.resolve(this.snapshot())
    this.available = null
    return this.run('check', async () => {
      const result = await this.updater.checkForUpdates()
      if (this.error) throw this.error
      if (!result || this.available === null || typeof result.updateInfo?.version !== 'string' || !result.updateInfo.version.trim()) throw Error('Update check did not return confirmed release information')
      this.version = result.updateInfo.version
      this.progress = null
      this.setState(this.available ? 'available' : 'current')
      return this.snapshot()
    })
  }
  async downloadFiles() {
    if (!this.nativeUpdater) return this.updater.downloadUpdate()
    let ready, failed
    const nativeReady = new Promise((resolve, reject) => {ready = resolve; failed = reject})
    this.nativeUpdater.once('update-downloaded', ready)
    this.nativeUpdater.once('error', failed)
    try {
      const [files] = await Promise.all([Promise.resolve().then(() => this.updater.downloadUpdate()), nativeReady])
      return files
    } finally {
      this.nativeUpdater.removeListener('update-downloaded', ready)
      this.nativeUpdater.removeListener('error', failed)
    }
  }
  download() {
    if (this.pending) {
      return this.operation === 'download' ? this.pending : Promise.reject(Error('Update check is still in progress'))
    }
    if (this.state !== 'available') return Promise.reject(Error('No confirmed update is available'))
    this.progress = 0
    return this.run('download', async () => {
      const files = await this.downloadFiles()
      if (this.error) throw this.error
      if (!Array.isArray(files) || !files.length || files.some(file => typeof file !== 'string' || !file)) throw Error('Update download did not return confirmed files')
      this.progress = 100
      this.setState('downloaded')
      return this.snapshot()
    })
  }
}
