import {createContext} from 'react'
import type {SessionChannel} from '@/hooks/useSessionSocket'

/** 复用主会话连接，消息行不能自行建立第二条连接。 */
export const MessageEditContext = createContext<{
  sessionId: string | null
  channel: Pick<SessionChannel, 'send' | 'steer' | 'connection'>
} | null>(null)
