/** Window-owned, unsubmitted feedback; never stores approval decisions. */
import { createWindowDraftStorage } from './windowDraftStorage'
const storage = createWindowDraftStorage()
const protectedRecords = new Set<string>()
const key = (sessionId: string, questionId: string) => `harness.plan-feedback.v1:${JSON.stringify([sessionId, questionId])}`
const valid = (value: unknown): value is string => typeof value === 'string' && value.length <= 100_000

export function readPlanFeedback(sessionId: string | null, questionId: string): string {
  if (!sessionId) return ''
  const id = key(sessionId, questionId)
  try {
    const raw = storage.read(id)
    if (raw === null) return ''
    const value: unknown = JSON.parse(raw)
    if (typeof value === 'string' && (valid(value) || storage.unpersisted(id))) return value
  } catch { /* 损坏原件保持不动，当前页仍可编辑新意见。 */ }
  protectedRecords.add(id)
  return ''
}

export const planFeedbackProtected = (sessionId: string | null, questionId: string): boolean =>
  Boolean(sessionId && protectedRecords.has(key(sessionId, questionId)))

export const planFeedbackUnpersisted = (sessionId: string | null, questionId: string): boolean =>
  Boolean(sessionId && storage.unpersisted(key(sessionId, questionId)))

export function savePlanFeedback(sessionId: string | null, questionId: string, text: string): boolean {
  if (!sessionId) return false
  const id = key(sessionId, questionId)
  return storage.write(id, text ? JSON.stringify(text) : null, valid(text) && !protectedRecords.has(id))
}

export function clearPlanFeedback(sessionId: string | null, questionId: string): void {
  if (!sessionId) return
  const id = key(sessionId, questionId)
  storage.write(id, null, !protectedRecords.has(id))
}
