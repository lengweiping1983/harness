import type {HumanRequester} from './protocol.generated'
import {sha256} from '@noble/hashes/sha2.js'

/** 邀请密钥只从 fragment 读一次，并在接口请求之前移除。 */
export function takeSharedToken(): string {
  const value = new URLSearchParams(window.location.hash.slice(1)).get('token') ?? ''
  if (window.location.hash) window.history.replaceState(null, '', window.location.pathname + window.location.search)
  return value
}

/** 只比较服务端凭据摘要来确认未知写结果，不自动重放登录。 */
export async function sharedCredentialId(token: string): Promise<string> {
  const digest = sha256(new TextEncoder().encode(token))
  return [...digest].map(byte => byte.toString(16).padStart(2, '0')).join('')
}


/** HTTP 只用于明确私有 IPv4 的局域网测试；HTTPS 仍可使用登记域名。 */
export function sharingOrigin(value: string): string | undefined {
  try {
    const url = new URL(value)
    const ip = url.hostname.split('.').map(Number)
    const privateIp = /^\d+\.\d+\.\d+\.\d+$/.test(url.hostname) && ip.length === 4 && ip.every(n => n >= 0 && n <= 255) &&
      (ip[0] === 10 || ip[0] === 127 || ip[0] === 192 && ip[1] === 168 || ip[0] === 172 && ip[1] >= 16 && ip[1] <= 31)
    if ((url.protocol !== 'https:' && !(url.protocol === 'http:' && privateIp)) || url.pathname !== '/' || url.search || url.hash || url.username || url.password) return
    return url.origin
  } catch {return}
}

export function requestKey(): string {
  return [...crypto.getRandomValues(new Uint8Array(16))].map(byte => byte.toString(16).padStart(2, '0')).join('')
}

/** 真人身份仅来自服务端的消息快照，显示名不能替换主体。 */
export function readRequester(value: unknown): HumanRequester | undefined {
  if (!value || typeof value !== 'object') return
  const row = value as Record<string, unknown>
  if (typeof row.subject === 'string' && row.subject && typeof row.display_name === 'string' && row.display_name) return {subject: row.subject, display_name: row.display_name}
}
