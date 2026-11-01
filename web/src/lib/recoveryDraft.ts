import { createWindowDraftStorage } from './windowDraftStorage'

type Mode = 'inspect' | 'continue'
export interface RecoveryEditorDraft {
  mode: Mode
  instructions: Partial<Record<Mode, string>>
  note: string
  sourceVersion: string | null
}
const storage = createWindowDraftStorage()
const key = (session: string, run: string) => `harness.recovery-draft.v1:${JSON.stringify([session, run])}`
export const recoveryDraftUnpersisted = (session: string, run: string) => storage.unpersisted(key(session, run))

/** Tab-owned text only. Verification decisions are deliberately never restored. */
export function readRecoveryDraft(session: string, run: string): RecoveryEditorDraft | null {
  const id = key(session, run)
  try {
    const raw = storage.read(id)
    if (!raw || raw.length > 100_000 && !storage.unpersisted(id)) return null
    const value = JSON.parse(raw)
    if (!value || !['inspect', 'continue'].includes(value.mode) || !value.instructions ||
      typeof value.note !== 'string' || value.sourceVersion !== null && typeof value.sourceVersion !== 'string') return null
    const instructions: RecoveryEditorDraft['instructions'] = {}
    for (const mode of ['inspect', 'continue'] as const) {
      if (typeof value.instructions[mode] === 'string') instructions[mode] = value.instructions[mode]
    }
    return { mode: value.mode, instructions, note: value.note, sourceVersion: value.sourceVersion }
  } catch { return null }
}

export function saveRecoveryDraft(session: string, run: string, draft: RecoveryEditorDraft): boolean {
  const raw = JSON.stringify({ mode: draft.mode, instructions: draft.instructions, note: draft.note, sourceVersion: draft.sourceVersion })
  return storage.write(key(session, run), raw, raw.length <= 100_000)
}

export function clearRecoveryDraft(session: string, run: string): boolean {
  return storage.write(key(session, run), null)
}

/** 只收尾提交时未被修改的草稿；保留重开后的编辑和另一种恢复意图。 */
export function clearSubmittedRecoveryDraft(session: string, run: string, mode: Mode, expected: RecoveryEditorDraft): boolean {
  const draft = readRecoveryDraft(session, run)
  if (!draft) return true
  if (draft.instructions[mode] !== expected.instructions[mode] || draft.sourceVersion !== expected.sourceVersion
      || mode === 'continue' && draft.note !== expected.note) return true
  delete draft.instructions[mode]
  if (mode === 'continue') draft.note = ''
  if (!Object.keys(draft.instructions).length && !draft.note) return clearRecoveryDraft(session, run)
  draft.mode = mode === 'inspect' ? 'continue' : 'inspect'
  return saveRecoveryDraft(session, run, draft)
}
