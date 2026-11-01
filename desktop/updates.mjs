export class DesktopUpdates {
  constructor({updater, packaged, notify = () => {}}) {
    this.updater = updater
    this.packaged = packaged
    this.notify = notify
    this.state = packaged ? 'idle' : 'development'
    this.pending = null
    updater.autoDownload = false
    updater.autoInstallOnAppQuit = true
    updater.allowPrerelease = false
    updater.on('update-available', () => {this.available = true})
    updater.on('update-not-available', () => {this.available = false})
    updater.on('error', () => {this.state = 'error'; notify(this.state)})
    updater.on('update-downloaded', () => {this.state = 'downloaded'; notify(this.state)})
  }
  check() {
    if (!this.packaged) return Promise.resolve({state:'development'})
    if (this.pending) return this.pending
    if (this.state === 'downloaded') return Promise.resolve({state:this.state})
    this.available = false
    this.state = 'checking'; this.notify(this.state)
    this.pending = this.updater.checkForUpdates().then(result => {
      if (!result) throw Error('Update check did not return release information')
      const available = Boolean(this.available)
      this.state = available ? 'available' : 'current'
      this.notify(this.state)
      return {state:this.state, version:result?.updateInfo?.version}
    }).catch(error => {this.state = 'error'; this.notify(this.state); throw error}).finally(() => {this.pending = null})
    return this.pending
  }
  download() {
    if (this.pending) return this.pending
    if (this.state !== 'available') return Promise.reject(Error('No confirmed update is available'))
    this.state = 'downloading'; this.notify(this.state)
    this.pending = this.updater.downloadUpdate().then(() => {
      this.state = 'downloaded'; this.notify(this.state)
      return {state:this.state}
    }).catch(error => {this.state = 'error'; this.notify(this.state); throw error}).finally(() => {this.pending = null})
    return this.pending
  }
}
