import {api} from '@/lib/api'
import {ApiError} from '@/lib/http'
import {useChatStore} from '@/store/chat'
import type {GoalState} from '@/types'

type Control = Parameters<typeof api.controlGoal>[1]

/** 回查只更新已保存状态，不能证明消息、广播及调度均已完成；不重放写入。 */
export async function controlGoal(sessionId: string, request: Control): Promise<GoalState> {
  const result = await api.controlGoal(sessionId, request)
  if (result.ok) {
    useChatStore.getState().apply(sessionId, {type: 'goal/state', ...result.data})
    return result.data
  }
  const fresh = await api.goal(sessionId)
  if (fresh.ok) {
    useChatStore.getState().apply(sessionId, {type: 'goal/state', ...fresh.data})
  }
  throw new ApiError(result)
}
