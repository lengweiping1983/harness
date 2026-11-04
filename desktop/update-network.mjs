/** Chromium 与 Node 的原始机器错误码共用分类，不按产品提示原文猜测错误。 */
export function updateNetworkKind(error) {
  const pending = [error], seen = new Set()
  while (pending.length) {
    const current = pending.shift()
    if (!current || seen.has(current)) continue
    seen.add(current)
    const code = typeof current.code === 'string' ? current.code : /\bnet::(ERR_[A-Z_]+)\b/.exec(current.message || '')?.[1]
    if (['ENOTFOUND','EAI_AGAIN','ERR_NAME_NOT_RESOLVED','ERR_NAME_RESOLUTION_FAILED'].includes(code)) return 'Dns'
    if (['ERR_PROXY_CONNECTION_FAILED','ERR_TUNNEL_CONNECTION_FAILED','ERR_NO_SUPPORTED_PROXIES','ERR_PROXY_AUTH_REQUESTED','ERR_MANDATORY_PROXY_CONFIGURATION_FAILED'].includes(code)) return 'Proxy'
    if (code?.startsWith('ERR_CERT_') || ['CERT_HAS_EXPIRED','UNABLE_TO_VERIFY_LEAF_SIGNATURE','SELF_SIGNED_CERT_IN_CHAIN','DEPTH_ZERO_SELF_SIGNED_CERT','ERR_SSL_PROTOCOL_ERROR'].includes(code)) return 'Tls'
    if (code === 'ERR_INTERNET_DISCONNECTED' || ['ENETUNREACH','EHOSTUNREACH'].includes(code)) return 'Offline'
    if (current.name === 'TimeoutError' || ['ETIMEDOUT','UND_ERR_CONNECT_TIMEOUT','ERR_TIMED_OUT','ERR_CONNECTION_TIMED_OUT'].includes(code)) return 'Timeout'
    if (['ECONNREFUSED','ECONNRESET','ERR_CONNECTION_RESET','ERR_CONNECTION_REFUSED','ERR_CONNECTION_CLOSED','ERR_NETWORK_CHANGED'].includes(code)) return 'Connection'
    if (current.cause) pending.push(current.cause)
    if (Array.isArray(current.errors)) pending.push(...current.errors)
  }
  return null
}

export class UpdateNetworkFailure extends Error {
  constructor(cause, url, classificationCause = cause) {
    const kind = updateNetworkKind(classificationCause)
    super(cause.message, {cause})
    this.menuKey = kind ? `updateNetwork${kind}` : null
    this.params = {host:new URL(url).hostname}
  }
}

/** 取消只结束代理路由观察；迟到的只读结果不能再触发备用传输。 */
async function proxyRoute(session, url, signal) {
  if (!signal) return session.resolveProxy(url)
  signal.throwIfAborted()
  let abort
  const cancelled = new Promise((_resolve, reject) => {
    abort = () => reject(signal.reason)
    signal.addEventListener('abort', abort, {once:true})
  })
  try {return await Promise.race([session.resolveProxy(url), cancelled])}
  finally {signal.removeEventListener('abort', abort)}
}

/** 更新检查与安装包传输共用系统代理；只有明确直连时才允许 DNS 备用路径。 */
export function createUpdateFetch(session, {directFetch = globalThis.fetch} = {}) {
  return async (url, options = {}) => {
    const settings = {...options, credentials:'omit', cache:'no-store'}
    try {return await session.fetch(url, settings)}
    catch (original) {
      if (options.signal?.aborted) throw options.signal.reason
      if ((!options.method || options.method.toUpperCase() === 'GET') && updateNetworkKind(original) === 'Dns') {
        let route
        try {route = await proxyRoute(session, url, options.signal)}
        catch (cause) {
          if (options.signal?.aborted) throw options.signal.reason
          throw new UpdateNetworkFailure(new AggregateError([original,cause], `${original.message}\n${cause.message}`), url)
        }
        if (options.signal?.aborted) throw options.signal.reason
        if (typeof route === 'string' && route.trim() === 'DIRECT') {
          try {return await directFetch(url, settings)}
          catch (cause) {
            if (options.signal?.aborted) throw options.signal.reason
            // 两条已结束的只读请求保留各自诊断，不能将失败当成无更新。
            throw new UpdateNetworkFailure(new AggregateError([original,cause], `${original.message}\n${cause.message}`), url, cause)
          }
        }
      }
      throw new UpdateNetworkFailure(original, url)
    }
  }
}

export function networkSettingsURL(platform = process.platform) {
  return {darwin:'x-apple.systempreferences:com.apple.preference.network', win32:'ms-settings:network-status'}[platform] || null
}
