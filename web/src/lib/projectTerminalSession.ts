import {api} from './api'
import {ApiError} from './http'

const sessions = new Map<string, Promise<string>>()

/** 为没有对话的项目复用终端所需的会话，不发送消息或启动模型。 */
export function projectTerminalSession(cwd?: string): Promise<string> {
  const key = cwd || ''
  const existing = sessions.get(key)
  if (existing) return existing
  const pending = api.createSession(cwd ? {cwd} : {}).then(result => {
    if (!result.ok) throw new ApiError(result)
    return result.data.session.id
  }).catch(error => {sessions.delete(key); throw error})
  sessions.set(key, pending)
  return pending
}
