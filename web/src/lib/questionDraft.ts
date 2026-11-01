import type { QuestionItem } from '@/types'
import { allowsCustomAnswer } from './questionAnswers'
import { createWindowDraftStorage } from './windowDraftStorage'

export interface QuestionDraft { selected: Record<string, string[]>; text: Record<string, string> }
const empty = (): QuestionDraft => ({ selected: {}, text: {} })
const storage = createWindowDraftStorage()
const protectedRecords = new Set<string>()
const key = (session: string, question: string) => `harness.question-draft.v1:${JSON.stringify([session, question])}`
export const draftableQuestions = (questions: QuestionItem[]) => questions.length > 0 && questions.every(allowsCustomAnswer)
const schema = (questions: QuestionItem[]) => JSON.stringify(questions.map((q) => [q.id, q.question, q.header, Boolean(q.multi_select), q.options?.map((o) => o.label ?? String(o.id ?? ''))]))

const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
export const questionDraftProtected = (session: string | null, question: string): boolean => Boolean(session && protectedRecords.has(key(session, question)))

export function readQuestionDraft(session: string | null, question: string, questions: QuestionItem[]): QuestionDraft {
  if (!session || !draftableQuestions(questions)) return empty()
  const id = key(session, question)
  try {
    const raw = storage.read(id)
    if (raw === null) return empty()
    if (raw.length > 100_000 && !storage.unpersisted(id)) throw Error('Invalid draft size')
    const value: unknown = JSON.parse(raw)
    if (!record(value) || value.schema !== schema(questions) || !record(value.selected) || !record(value.text)
      || Object.keys(value).some(field => !['schema', 'selected', 'text'].includes(field))) throw Error('Invalid draft envelope')
    const byId = new Map(questions.map(q => [q.id, q]))
    for (const [id, selected] of Object.entries(value.selected)) {
      const q = byId.get(id)
      const labels = new Set(q?.options?.map(o => o.label ?? String(o.id ?? '')) ?? [])
      if (!q || !Array.isArray(selected) || !selected.every(v => typeof v === 'string' && labels.has(v))
        || new Set(selected).size !== selected.length || !q.multi_select && selected.length > 1) throw Error('Invalid draft selection')
    }
    for (const [id, text] of Object.entries(value.text)) {
      if (!byId.has(id) || typeof text !== 'string') throw Error('Invalid draft text')
    }
    return {selected: value.selected as QuestionDraft['selected'], text: value.text as QuestionDraft['text']}
  } catch {
    protectedRecords.add(id)
    return empty()
  }
}

export function saveQuestionDraft(session: string | null, question: string, questions: QuestionItem[], draft: QuestionDraft): boolean {
  if (!session || !draftableQuestions(questions)) return true
  const id = key(session, question)
  if (!Object.values(draft.text).some(Boolean) && !Object.values(draft.selected).some((v) => v.length)) {
    return clearQuestionDraft(session, question)
  }
  const raw = JSON.stringify({ schema: schema(questions), ...draft })
  return storage.write(id, raw, raw.length <= 100_000 && !protectedRecords.has(id))
}

export function clearQuestionDraft(session: string | null, question: string): boolean {
  if (!session) return true
  const id = key(session, question)
  return storage.write(id, null, !protectedRecords.has(id))
}
