export interface PlanReaderState { selected: string; followLatest: boolean; compare: boolean }
const initial: PlanReaderState = { selected: '', followLatest: true, compare: false }
const key = (sessionId: string) => `harness.plan-reader.v1:${sessionId}`

/** Window-owned reading state; persisted IDs are checked against the server list. */
export function readPlanReader(sessionId: string | null): PlanReaderState {
  if (!sessionId) return { ...initial }
  try {
    const value = JSON.parse(sessionStorage.getItem(key(sessionId)) ?? 'null')
    if (value && typeof value.selected === 'string' && value.selected.length <= 200
      && typeof value.followLatest === 'boolean' && typeof value.compare === 'boolean') return value
  } catch { /* Reading still works when browser storage is unavailable. */ }
  return { ...initial }
}

export function savePlanReader(sessionId: string | null, state: PlanReaderState): void {
  if (!sessionId) return
  try { sessionStorage.setItem(key(sessionId), JSON.stringify(state)) } catch { /* Optional persistence. */ }
}

export function selectedPlan(current: string, followLatest: boolean, ids: string[]): string {
  return followLatest || !ids.includes(current) ? ids.at(-1) ?? '' : current
}

const scrollKey = (sessionId: string, planId: string) => `harness.plan-scroll.v1:${JSON.stringify([sessionId, planId])}`
const validScroll = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 10_000_000

/** Position metadata only; the plan body is always read again from the server. */
export function readPlanScroll(sessionId: string, planId: string): number {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(scrollKey(sessionId, planId)) ?? 'null')
    return validScroll(value) ? value : 0
  } catch { return 0 }
}

export function savePlanScroll(sessionId: string, planId: string, position: number): void {
  if (!validScroll(position)) return
  try { sessionStorage.setItem(scrollKey(sessionId, planId), JSON.stringify(position)) } catch { /* Optional persistence. */ }
}
