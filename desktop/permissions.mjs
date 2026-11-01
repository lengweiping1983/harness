import {randomUUID} from 'node:crypto'
import {NativeBrowserError} from './browser-errors.mjs'

const supported = new Set(['notifications', 'geolocation', 'geolocation-approximate', 'clipboard-read', 'clipboard-sanitized-write', 'media'])
export function websiteOrigin(url) {
  try {const parsed = new URL(url); return /^https?:$/.test(parsed.protocol) ? parsed.origin : null} catch {return null}
}
function capabilities(permission, details) {
  if (!supported.has(permission)) return []
  if (permission !== 'media') return [permission]
  const types = details.mediaTypes || (details.mediaType ? [details.mediaType] : [])
  return types.length && types.every(type => ['audio', 'video'].includes(type)) ? [...new Set(types)].map(type => type === 'audio' ? 'microphone' : 'camera') : []
}
export class WebsitePermissions {
  constructor(changed = () => {}) {this.changed = changed; this.pending = new Map(); this.grants = new Map()}
  key(tab, origin) {return JSON.stringify([tab, origin])}
  snapshot(tab, url) {
    const origin = websiteOrigin(url)
    return {origin, grants:[...(this.grants.get(this.key(tab, origin)) || [])], requests:[...this.pending.values()].filter(request => request.tab === tab && request.origin === origin).map(({id, origin, capabilities}) => ({id, origin, capabilities}))}
  }
  check(tab, url, requestingURL, permission, details = {}) {
    const origin = websiteOrigin(url)
    const names = capabilities(permission, details)
    return Boolean(origin && origin === websiteOrigin(requestingURL) && names.length && names.every(name => this.grants.get(this.key(tab, origin))?.has(name)))
  }
  request(tab, url, permission, callback, details = {}) {
    const origin = websiteOrigin(url)
    const names = capabilities(permission, details)
    if (!origin || origin !== websiteOrigin(details.requestingUrl) || !names.length || this.pending.size >= 20) {callback(false); return}
    if (this.check(tab, url, details.requestingUrl, permission, details)) {callback(true); return}
    const id = randomUUID()
    const timer = setTimeout(() => this.finish(id, false), 60000)
    timer.unref()
    this.pending.set(id, {id, tab, origin, capabilities:names, callback, timer})
    this.changed()
  }
  _detach(id) {
    const request = this.pending.get(id)
    if (request) {this.pending.delete(id); clearTimeout(request.timer)}
    return request
  }
  finish(id, allow) {
    const request = this._detach(id)
    if (!request) return
    if (allow) {
      const key = this.key(request.tab, request.origin)
      const grants = this.grants.get(key) || new Set()
      request.capabilities.forEach(name => grants.add(name))
      this.grants.set(key, grants)
    }
    try {request.callback(allow)} finally {this.changed()}
  }
  answer(tab, url, id, allow) {
    const request = this.pending.get(id)
    if (!request || request.tab !== tab || request.origin !== websiteOrigin(url)) throw new NativeBrowserError('permissionInactive')
    if (typeof allow !== 'boolean') throw new NativeBrowserError('permissionInvalid')
    this.finish(id, allow)
  }
  clear(tab) {
    const requests = []
    for (const [id, request] of this.pending) if (request.tab === tab) requests.push(this._detach(id))
    for (const key of this.grants.keys()) if (JSON.parse(key)[0] === tab) this.grants.delete(key)
    // 先释放整页状态，再通知外部回调；单个失效回调不能阻断其他请求清理。
    for (const request of requests) {
      try {request.callback(false)} catch (error) {console.error('Website permission cancellation failed:', error)}
    }
    this.changed()
  }
}
