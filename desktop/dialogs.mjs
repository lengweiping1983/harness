import {randomUUID} from 'node:crypto'
import {NativeBrowserError} from './browser-errors.mjs'

export class NativeDialogs {
  constructor(changed = () => {}, unsupported = message => console.warn(message)) {this.pending = new Map(); this.changed = changed; this.unsupported = unsupported}
  watch(tab, contents) {
    // 私有事件只在已知的默认处理器结构下接管；不支持时保留 Electron 默认行为。
    const handlers = contents.rawListeners('-run-dialog')
    if (handlers.length !== 1) {
      this.unsupported('Unsupported Electron dialog adapter; retaining native dialog handling')
      return false
    }
    contents.removeListener('-run-dialog', handlers[0])
    contents.on('-run-dialog', (info, answer) => {
      this.open(tab, info.dialogType, info.messageText, info.frame.url, info.defaultPromptText, answer)
    })
    contents.on('-cancel-dialogs', () => this.clear(tab))
    return true
  }
  open(tab, type, message, url, defaultValue, answer, messageCopy = null) {
    if (this.pending.has(tab)) {answer(false, ''); return}
    this.pending.set(tab, {id:randomUUID(), type, message:String(message).slice(0, 4000), ...(messageCopy ? {message_copy:messageCopy} : {}), url, default_value:String(defaultValue || '').slice(0, 2000), response_unconfirmed:false, responding:false, answer})
    this.changed()
  }
  snapshot(tab) {const entry = this.pending.get(tab); if (!entry) return null; const {answer, ...view} = entry; return view}
  clear(tab) {if (this.pending.delete(tab)) this.changed()}
  async respond(tab, contents, args) {
    const entry = this.pending.get(tab)
    if (!entry || entry.id !== args.id || contents.isDestroyed()) throw new NativeBrowserError('inactive')
    if (entry.responding || entry.response_unconfirmed) throw new NativeBrowserError('pending')
    if (typeof args.accept !== 'boolean' || (entry.type === 'prompt' && args.accept && (typeof args.prompt_text !== 'string' || args.prompt_text.length > 2000))) throw new NativeBrowserError('invalid')
    entry.responding = true
    this.changed()
    try {
      this.pending.delete(tab)
      entry.answer(args.accept, entry.type === 'prompt' && args.accept ? args.prompt_text : '')
      if (this.pending.get(tab) === entry) this.clear(tab)
    } catch (error) {entry.response_unconfirmed = true; if (!this.pending.has(tab)) this.pending.set(tab, entry); throw error} finally {entry.responding = false; this.changed()}
  }
}
