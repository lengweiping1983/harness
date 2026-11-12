import { t } from '@/i18n'
import { ApiError, isUnconfirmedWrite } from './http'
import { failureText } from './resourceError'
import type { Block, ResourceText } from '@/types'

export interface QuestionSubmissionFeedback { title: ResourceText; message: ResourceText; unconfirmed: boolean }
export const questionSubmissionKey = (sessionId: string | null, questionId: string) => JSON.stringify([sessionId, questionId])

export function assertQuestionAnswerAccepted(receipt: Record<string, unknown>): void {
  if (receipt.resolved === false) throw new ApiError({ ok: false, status: 409, code: 'question/conflict',
    message: t("ui.lib.questionSubmission.b9d682b36184"), copy: {key: "ui.lib.questionSubmission.b9d682b36184"} })
}

export function questionAnswerConfirmed(blocks: Block[], questionId: string): boolean {
  return blocks.some((block) => block.kind === 'question' && block.questionId === questionId && block.answered && !block.closed)
}

export function questionSubmissionFeedback(error: unknown): QuestionSubmissionFeedback {
  const unconfirmed = !(error instanceof ApiError) || isUnconfirmedWrite(error.failure) || error.failure.code === 'question/unavailable'
  return { unconfirmed, title: {key: unconfirmed ? "ui.lib.questionSubmission.6ead3e29ddbd" : "ui.lib.questionSubmission.29e188fd3ee5"},
    message: failureText(error) }
}
