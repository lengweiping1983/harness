import type { GoalState } from '@/types'
type Goal = Pick<NonNullable<GoalState['goal']>, 'objective' | 'criteria'>

/** 目标的独立验收条件合并到正文，重复的隐式目标只显示一次。 */
export function goalInputText(goal?: Goal | null): string {
  if (!goal) return ''
  const separate = (goal.criteria ?? []).filter(criterion => criterion.trim() !== goal.objective.trim())
  return separate.length ? `${goal.objective}\n\n${separate.map(criterion => `- ${criterion}`).join('\n')}` : goal.objective
}

/** 未改正文时保留原条件与证据；修改正文时以完整输入重新验收。 */
export function goalInputFields(input: string, goal?: Goal | null): { objective: string; success_criteria?: null } {
  if (goal && input.trim() === goalInputText(goal).trim()) return { objective: goal.objective }
  return { objective: input.trim(), ...(goal ? { success_criteria: null } : {}) }
}
