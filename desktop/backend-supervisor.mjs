import {stopChild} from './stop-child.mjs'
import {startBackend} from './backend.mjs'

export class BackendSupervisor {
  constructor(options, notify, launch = startBackend) {
    this.options = options
    this.notify = notify
    this.launch = launch
    this.backend = null
    this.pending = null
    this.status = 'starting'
    this.closing = false
  }
  update(status) { this.status = status; this.notify(status) }
  start() {
    if (this.closing) return Promise.reject(Error('Desktop is closing'))
    if (this.pending) return this.pending
    if (this.backend?.child.exitCode === null && this.backend.child.signalCode === null) return Promise.resolve(this.backend)
    this.update('starting')
    this.pending = this.launch(this.options).then(backend => {
      this.backend = backend
      // 迟到的启动结果仍归监管器所有，统一由 close 停止并报告清理失败。
      if (this.closing) throw Error('Desktop is closing')
      this.options.port = backend.port
      backend.child.once('exit', () => {if (!this.closing && this.backend === backend) this.update('stopped')})
      if (backend.child.exitCode !== null || backend.child.signalCode !== null) throw Error('Local service exited during startup')
      this.update('running')
      return backend
    }).catch(error => {if (!this.closing) this.update('stopped'); throw error}).finally(() => {this.pending = null})
    return this.pending
  }
  close() {
    if (this.closePromise) return this.closePromise
    this.closing = true
    this.closePromise = (async () => {
      if (this.pending) await this.pending.catch(() => {})
      await stopChild(this.backend?.child)
    })()
    return this.closePromise
  }
}
