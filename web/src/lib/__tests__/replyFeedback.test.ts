import { describe, expect, it } from 'vitest'
import type { QuestionBlock } from '@/types'
import { replyFeedback } from '../replyFeedback'
import { readGeneration } from '../generation'

const question = (id: string, extra = {}): QuestionBlock => ({
  id: 'q-' + id, kind: 'question', at: 1, questionId: id,
  questions: [{ id, question: 'Please decide', options: [] }], ...extra,
})
const generation = readGeneration({ request_id: 'old', phase: 'waiting' })

describe('interactive waiting feedback', () => {
  it('prioritizes permission over stale streaming and generation state', () => {
    expect(replyFeedback({ blocks: [question('permission')], running: true, streaming: true,
      sending: false, generation })).toBe('ui.reply.approval')
  })
  it('keeps ordinary questions actionable even when runtime is idle', () => {
    expect(replyFeedback({ blocks: [question('choice')], running: false, streaming: false,
      sending: false })).toBe('ui.reply.input')
  })
  it('does not confuse answered or closed prompts with current approval', () => {
    expect(replyFeedback({ blocks: [question('permission', { answered: true }), question('choice', { closed: true })],
      running: true, streaming: false, sending: false, generation })).toBe('ui.reply.waiting')
  })
})
