import type { QuestionItem, QuestionBlock, ToolBlockState } from '@/types'

export function allowsCustomAnswer(question: QuestionItem): boolean {
  return question.id !== 'plan-review' && question.id !== 'permission'
}

export function questionReady(question: QuestionItem, selected: string[], text: string): boolean {
  if (!question.options?.length) return Boolean(text.trim())
  if (question.id === 'plan-review' && question.options.some(option => option.id === 'approve' && option.label !== undefined && selected.includes(option.label)) && text.trim()) return false
  return selected.length > 0 || (allowsCustomAnswer(question) && Boolean(text.trim()))
}

/** Reconstruct a read-only form only from a completed, persisted tool record. */
const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const nonblank = (value: unknown): value is string => typeof value === 'string' && Boolean(value.trim())
function validHistoricalQuestion(value: unknown): value is QuestionItem {
  if (!record(value) || !nonblank(value.id) || !nonblank(value.question)
    || (value.header !== undefined && typeof value.header !== 'string')
    || (value.multi_select !== undefined && typeof value.multi_select !== 'boolean')) return false
  if (value.options === undefined) return true
  if (!Array.isArray(value.options)) return false
  const labels = new Set<string>()
  for (const option of value.options) {
    if (!record(option) || !nonblank(option.label) || labels.has(option.label)
      || (option.description !== undefined && typeof option.description !== 'string')) return false
    labels.add(option.label)
  }
  return true
}

export function savedQuestionAnswers(block: ToolBlockState): QuestionBlock | null {
  if (block.name !== 'ask_user_question' || block.status !== 'ok') return null
  const input = block.input
  const result = block.display ?? block.output
  if (!record(input) || !record(result) || !Array.isArray(input.questions) || !input.questions.length || !Array.isArray(result.answers)) return null
  const questions = input.questions
  if (!questions.every(validHistoricalQuestion) || !questions.every(allowsCustomAnswer)) return null
  const byId = new Map(questions.map((question) => [question.id, question]))
  if (byId.size !== questions.length || result.answers.length !== questions.length) return null
  const answered = new Set<string>()
  for (const answer of result.answers) {
    if (!record(answer) || typeof answer.id !== 'string' || answered.has(answer.id)
      || !Array.isArray(answer.selected) || !answer.selected.every((value: unknown) => typeof value === 'string')
      || (answer.custom !== undefined && typeof answer.custom !== 'string')) return null
    const question = byId.get(answer.id)
    if (!question || (!question.multi_select && answer.selected.length > 1)
      || new Set(answer.selected).size !== answer.selected.length) return null
    const labels = new Set(question.options?.map((option) => option.label) ?? [])
    if (!answer.selected.every((value) => labels.has(value))) return null
    answered.add(answer.id)
  }
  return { kind: 'question', id: `saved-question-${block.id}`, at: block.at, questionId: `history:${block.callId}`,
    questions, answered: true, answers: result.answers }
}
