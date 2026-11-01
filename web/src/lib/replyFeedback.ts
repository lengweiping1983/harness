import type { Block } from '@/types'
import type { GenerationState } from './generation'

/** 待回答问题优先于旧的模型流状态，权限问题与普通提问分别提示。 */
export function pendingReplyInput(blocks: readonly Block[]): string | null {
  const pending = blocks.filter(block => block.kind === 'question' && !block.answered && !block.closed)
  if (!pending.length) return null
  return pending.some(block => block.kind === 'question' && block.questions.some(question => question.id === 'permission'))
    ? 'ui.reply.approval' : 'ui.reply.input'
}

/** 只显示发送与真实运行状态，不把界面反馈伪装成模型回复。 */
export function replyFeedback({ blocks, running, streaming, sending, generation }: { blocks: readonly Block[]; running: boolean; streaming: boolean; sending: boolean; generation?: GenerationState | null }): string | null {
  const input = pendingReplyInput(blocks)
  if (input) return input
  if (streaming) return null
  if (sending && !running) return 'ui.reply.sending'
  if (!running) return null
  if (generation?.phase === 'retrying') return 'ui.reply.retrying'
  for (let index = blocks.length - 1; index >= 0; index--) {
    const block = blocks[index]!
    if (block.kind === 'user' && !block.steeringStatus) break
    if (block.kind === 'team') {
      if (block.phase === 'start' || block.phase === 'plan') return 'ui.reply.planning'
      if (block.phase === 'execute') return 'ui.reply.executing'
      if (block.phase === 'summary') return 'ui.reply.summarizing'
    }
  }
  return generation ? 'ui.reply.waiting' : 'ui.reply.preparing'
}
