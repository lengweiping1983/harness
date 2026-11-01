import {validPersistedDrafts} from './draftPersistence'
import {api} from './api'
import {t} from '@/i18n'
import type {SessionSummary} from '@/types'

export const creationUnconfirmed = () => ({ok: false as const, status: 0, code: 'session/creation-unconfirmed',
  message: t('ui.sessionCreation.unconfirmed'), copy: {key: 'ui.sessionCreation.unconfirmed'}})

export function createdFor(session: SessionSummary | undefined, requestId: string): boolean {
  return Boolean(session?.id && session.metadata?.creation_request_id === requestId && session.metadata.creation_completed === true)
}

/** 仅凭持久请求身份回查；不存在、不完整或重复匹配都不能重放创建。 */
export async function recoverSessionCreation(requestId: string): Promise<Awaited<ReturnType<typeof api.createSession>>> {
  try {
    const list = await api.listSessions()
    if (!list.ok) return creationUnconfirmed()
    const matches = list.data.sessions.filter(session => session.metadata?.creation_request_id === requestId)
    if (matches.length !== 1 || !createdFor(matches[0], requestId)) return creationUnconfirmed()
    const session = matches[0]
    const read = await api.getSession(session.id, 'conversation-window')
    if (!read.ok || read.data.header.id !== session.id || !createdFor(read.data.header, requestId)) return creationUnconfirmed()
    return {ok: true, data: {session: {...session, ...read.data.header},
      ...(read.data.agent_mount ? {agent_mount: read.data.agent_mount} : {})}}
  } catch { return creationUnconfirmed() }
}

/** 身份必须已写入真实浏览器存储，内存回退不足以支持刷新后的只读回查。 */
export function creationRetained(storageName: string | undefined, requestId: string): boolean {
  if (!storageName) return false
  try {
    const raw = localStorage.getItem(storageName)
    return raw !== null && validPersistedDrafts(raw) && JSON.parse(raw).state.bySession.__unsaved__?.creationRequest?.id === requestId
  } catch { return false }
}
