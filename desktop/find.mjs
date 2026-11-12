import {NativeBrowserError} from './browser-errors.mjs'

export class PageFind {
  constructor(contents, changed = () => {}) {
    this.contents = contents
    this.changed = changed
    this.state = {open:false, text:'', matches:0, ordinal:0}
    this.request = null
    contents.on('found-in-page', (_event, result) => {
      if (this.request !== result.requestId || !this.state.open) return
      this.state.matches = result.matches
      this.state.ordinal = result.activeMatchOrdinal
      this.changed()
    })
  }
  open() {this.state.open = true; this.changed()}
  search(text, forward = true, next = false) {
    if (typeof text !== 'string' || text.length > 1000) throw new NativeBrowserError('findInvalid', {count:1000})
    const fresh = text !== this.state.text || !next
    Object.assign(this.state, {open:true, text})
    if (fresh) Object.assign(this.state, {matches:0, ordinal:0})
    this.request = null
    if (text) this.request = this.contents.findInPage(text, {forward, findNext:fresh})
    else this.contents.stopFindInPage('clearSelection')
    this.changed()
  }
  close() {
    this.request = null
    Object.assign(this.state, {open:false, text:'', matches:0, ordinal:0})
    if (!this.contents.isDestroyed()) this.contents.stopFindInPage('clearSelection')
    this.changed()
  }
}
