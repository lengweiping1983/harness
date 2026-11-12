/** 领取证明仅保存在当前标签页，用于刷新后的只读回查；不重放申请。 */
const key = 'harness.invitation.pending.v1'
const lifetime = 24 * 60 * 60 * 1000
export type InvitationRecovery = {token: string; secret: string; expires: number}
export function readInvitationRecovery(): InvitationRecovery | undefined {
  try {
    const row = JSON.parse(sessionStorage.getItem(key) ?? 'null')
    if (row && typeof row.token === 'string' && row.token.startsWith('hi1.') && typeof row.secret === 'string' && /^[A-Za-z0-9_-]{43}$/.test(row.secret) && typeof row.expires === 'number' && row.expires > Date.now() && row.expires <= Date.now() + lifetime) return row
    sessionStorage.removeItem(key)
  } catch { /* 存储不可用时保持原有内存流程。 */ }
}
export function saveInvitationRecovery(token: string, secret: string): void {
  try {sessionStorage.setItem(key, JSON.stringify({token, secret, expires: Date.now() + lifetime}))} catch { /* 不阻止当前页面申请。 */ }
}
export function clearInvitationRecovery(token: string): void {
  if (readInvitationRecovery()?.token !== token) return
  try {sessionStorage.removeItem(key)} catch { /* 清理失败不触发网络写入。 */ }
}
