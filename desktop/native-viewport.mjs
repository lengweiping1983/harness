import {NativeBrowserError} from './browser-errors.mjs'

export class NativeViewports {
  constructor({timeout = 1500} = {}) {this.timeout = timeout; this.entries = new Map(); this.tokens = new Map(); this.generation = 0}
  key(view) {return JSON.stringify({bounds:view.getBounds(), zoom:view.webContents.getZoomFactor()})}
  peek(view) {if (view.webContents.isDestroyed()) return null; const entry = this.entries.get(String(view.webContents.id)); return entry?.key === this.key(view) ? entry.viewport : null}
  invalidate(id) {this.entries.delete(id)}
  forget(id) {this.entries.delete(id); this.tokens.delete(id)}
  clear() {this.entries.clear(); this.tokens.clear(); this.generation++}
  async get(view, {blocked = false} = {}) {
    const wc = view.webContents, id = String(wc.id)
    const generation = this.generation
    const key = this.key(view), prior = this.entries.get(id)
    if (blocked) return prior?.viewport || null
    if (prior?.key === key) return prior.viewport
    const token = this.tokens.get(id) || {}
    this.tokens.set(id, token)
    let timer
    try {
      const viewport = await Promise.race([
        wc.executeJavaScriptInIsolatedWorld(1001, [{code:"new Promise(resolve => {const read = () => resolve({width:window.innerWidth,height:window.innerHeight}); if (document.visibilityState === 'visible') requestAnimationFrame(read); else setTimeout(read, 0)})"}]),
        new Promise((_, reject) => {timer = setTimeout(() => reject(new NativeBrowserError('viewportTimeout')), this.timeout)}),
      ])
      if (viewport && (viewport.width === 0 || viewport.height === 0)) throw new NativeBrowserError('viewportPending')
      if (!viewport || !['width','height'].every(key => Number.isSafeInteger(viewport[key]) && viewport[key] > 0 && viewport[key] <= 32768)) throw new NativeBrowserError('viewportInvalid')
      if (wc.isDestroyed() || this.generation !== generation || this.tokens.get(id) !== token || this.key(view) !== key) throw new NativeBrowserError('viewportChanged')
      this.entries.set(id, {key, viewport})
      return viewport
    } finally {clearTimeout(timer)}
  }
}
