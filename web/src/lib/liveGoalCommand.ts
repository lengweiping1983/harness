import {api, unwrap} from './api'
import type {GoalState} from '@/types'
import {controlGoal} from './goalControl'

/** 控制命令直接使用目标接口，不进入普通任务队列。 */
export async function applyLiveGoalCommand(sessionId: string, text: string): Promise<GoalState> {
  const rest = text.trim().replace(/^\/goal\s*/, '').trim()
  const state = await unwrap(api.goal(sessionId))
  const goal = state.goal
  if (!rest || rest === 'status' || rest === 'help' || rest === 'edit') return state
  if (['pause', 'resume', 'clear', 'delete'].includes(rest)) {
    return controlGoal(sessionId, {action: rest === 'delete' ? 'clear' : rest, goal_id: goal?.id, revision: goal?.revision})
  }
  if (/^edit\s/.test(rest)) {
    const objective = rest.replace(/^edit\s+/, '').trim()
    return controlGoal(sessionId, {action: 'edit', goal_id: goal?.id, revision: goal?.revision, objective,
      ...(goal?.criteria?.length === 1 && goal.criteria[0] === goal.objective ? {success_criteria: [objective]} : {})})
  }
  // 相同目标的重试或双击不创建第二个目标，也不重置进度。
  if (goal && goal.phase !== 'complete' && goal.objective === rest) return state
  return controlGoal(sessionId, {action: 'create', objective: rest, max_goal_rounds: null})
}
