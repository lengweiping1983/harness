import {EventEmitter} from 'node:events'
import {NativeBrowserError} from './browser-errors.mjs'

export class PageReadiness extends EventEmitter {
  constructor() {super(); this.pending = new Set()}
  begin(id) {this.pending.add(id); this.changed()}
  finish(id) {this.pending.delete(id); this.changed()}
  changed() {this.emit('changed')}
  async waitSelected(selected, ready, timeout = 5000) {
    const deadline = Date.now() + timeout
    while (true) {
      const id = selected()
      if (!id || ready(id)) return
      if (!this.pending.has(id)) throw new NativeBrowserError('pageFailed')
      const remaining = deadline - Date.now()
      if (remaining <= 0) throw new NativeBrowserError('pageInitializing')
      await new Promise((resolve, reject) => {
        const changed = () => {clearTimeout(timer); resolve()}
        const timer = setTimeout(() => {this.removeListener('changed', changed); reject(new NativeBrowserError('pageInitializing'))}, remaining)
        this.once('changed', changed)
      })
    }
  }
}
