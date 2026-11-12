import { t, getLocale } from '@/i18n'
import type { GoalState } from '@/types'

export interface GoalReadiness {
  running: boolean
  stopping: boolean
  connected: boolean
  queuePaused: boolean
  queued: number
  planMode: boolean
  waiting: 'answer' | 'approval' | null
}
const reasons: Record<string, string> = {
  get 'token-budget'() { return t("ui.lib.goalActivity.84468557994f") },
  get 'token-accounting-error'() { return t("ui.lib.goalActivity.7f5c1a5c1687") },
  get 'round-limit'() { return t("ui.lib.goalActivity.5843184c006a") },
  get 'no-tool-progress'() { return t('ui.lib.goalActivity.ac7fa7e2b532') },
  get 'run-failed'() { return t("ui.lib.goalActivity.88bd412af942") },
  get 'interrupted'() { return t("ui.lib.goalActivity.c1e695c015b6") },
  get 'user-stopped'() { return t("ui.lib.goalActivity.11deb588ebd2") },
  get 'dispatch-error'() { return t("ui.lib.goalActivity.2582dde7139d") },
}

/** Goal activation is authority; actual chat gates explain why it is waiting. */
export function goalActivity(state: GoalState, context: GoalReadiness): { label: string; note: string } {
  const goal = state.goal
  if (!goal) return { label: t("ui.lib.goalActivity.181f81e0a512"), note: t("ui.lib.goalActivity.a5e9059f3281") }
  if (goal.phase === 'complete') return { label: t("ui.lib.goalActivity.f28461bb49c8"), note: t("ui.lib.goalActivity.2d853ad92e15") }
  if (!context.connected) return { label: t("ui.lib.goalActivity.dafcef4ed8d5"), note: t("ui.lib.goalActivity.0a7405336a6f") }
  if (context.stopping) return { label: t("ui.lib.goalActivity.af48159f380f"), note: t("ui.lib.goalActivity.a222fe96474a") }
  if (goal.phase === 'blocked') return { label: t("ui.lib.goalActivity.b7e3e715f18b"), note: goal.blockedReason?.message || t("ui.lib.goalActivity.819a55a72799") }
  if (goal.phase === 'paused') return { label: t("ui.lib.goalActivity.eb0c326b60ae"), note: context.running ? t("ui.lib.goalActivity.6cd018ca203c") : t("ui.lib.goalActivity.84a508a71501") }
  if (state.activation !== 'armed') {
    const capRaised = goal.stopReason === 'round-limit' && (goal.maxGoalRounds == null || goal.roundsStarted < goal.maxGoalRounds)
    return { label: t("ui.lib.goalActivity.20f8e5ca6d57"), note: capRaised ? t("ui.lib.goalActivity.2782aa6013f3") :
      (goal.stopReason && Object.hasOwn(reasons, goal.stopReason) && reasons[goal.stopReason]) || t("ui.lib.goalActivity.b3b3fc7359ef") }
  }
  if (context.waiting) return { label: context.waiting === 'approval' ? t("ui.lib.goalActivity.1e047f37e14f") : t("ui.lib.goalActivity.ed16bd1db4b6"), note: t("ui.lib.goalActivity.4354b4365be4") }
  if (context.planMode) return { label: t("ui.lib.goalActivity.73617552b7a3"), note: t("ui.lib.goalActivity.c2c472bf6a8f") }
  if (context.queuePaused) return { label: t("ui.lib.goalActivity.1673b3111f65"), note: context.running ? t("ui.lib.goalActivity.e4854d0df24a") : t("ui.lib.goalActivity.5506ebe7deda") }
  if (!context.running && context.queued > 0) return { label: t("ui.lib.goalActivity.1673b3111f65"), note: t("ui.lib.goalActivity.f958825df21c") }
  if (!context.running && goal.retryAt != null) return { label: t('ui.goal.retrying'), note: t('ui.goal.retryNote', {
    time: new Date(goal.retryAt * 1000).toLocaleTimeString(getLocale()), attempt: goal.retryCount ?? 1,
  }) }
  return { label: t("ui.lib.goalActivity.7acfe3750404"), note: context.running ? t("ui.lib.goalActivity.d6adcff0967c") : t("ui.lib.goalActivity.be57a5ae172e") }
}
