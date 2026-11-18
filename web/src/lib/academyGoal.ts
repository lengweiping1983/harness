import type {AcademyActivityDay} from '@/types'

const GOAL_KEY = 'harness.academy.goal.v1'
/** 目标变更事件：同一页面内多个消费组件（会话条/仪表盘/报告热图）同步重读。 */
export const ACADEMY_GOAL_EVENT = 'harness-academy-goal-changed'

/** 读取每日证据目标；损坏或越界存储回退为未设定，不隐式迁移。 */
export function academyDailyGoalRead(): number | null {
  try {
    const raw = JSON.parse(localStorage.getItem(GOAL_KEY) ?? 'null') as unknown
    return typeof raw === 'number' && Number.isInteger(raw) && raw >= 1 && raw <= 99 ? raw : null
  } catch { return null }
}
export function academyDailyGoalWrite(goal: number | null): void {
  try {localStorage.setItem(GOAL_KEY, JSON.stringify(goal))} catch { /* 存储不可用时仅本次会话生效 */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(ACADEMY_GOAL_EVENT))
}

/** 本地日期键（YYYY-MM-DD），与宿主 activity 的 time.localtime 口径一致。 */
export function academyTodayKey(now = new Date()): string {
  return now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' + String(now.getDate()).padStart(2, '0')
}
/** 今日真实学习证据数；无当日活动记录计 0，不投影。 */
export function academyTodayEvidence(activity?: ReadonlyArray<AcademyActivityDay>): number {
  const today = academyTodayKey()
  return activity?.find(day => day.date === today)?.evidence ?? 0
}
