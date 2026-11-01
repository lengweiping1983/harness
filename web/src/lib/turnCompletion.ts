import { ResourceError } from './resourceError'
import type { GroupedConversationRow } from './toolRuns'
import type { TurnActivity } from './turnActivity'
import type { TurnCompletion as Completion } from './protocol.generated'
import type { ConversationMessagePart } from '@/types'

export type TurnCompletion = Completion
export interface CompletionRow { kind: 'completion'; id: string; at: number; turn: number; endSeq: number; finalSeq: number | null }
export type CompletedConversationRow = GroupedConversationRow | CompletionRow

/** Put actions after all visible results, once per committed terminal turn. */
export function completionRows(rows: GroupedConversationRow[], turns: Record<number, TurnActivity>): CompletedConversationRow[] {
  const last = new Map<number, number>()
  rows.forEach((row, index) => {
    const turn = row.kind === 'block' ? row.block.turn : row.turn
    if (!turn) return
    last.set(turn, index)
  })
  const output: CompletedConversationRow[] = []
  rows.forEach((row, index) => {
    output.push(row)
    const turn = row.kind === 'block' ? row.block.turn : row.turn
    if (!turn || last.get(turn) !== index) return
    const activity = turns[turn]
    if (activity?.endSeq === undefined) return
    output.push({ kind: 'completion', id: `completion-turn-${turn}-${activity.endSeq}`, turn, endSeq: activity.endSeq!,
      finalSeq: activity.finalResponseSeq ?? null, at: activity.endedAt ?? (row.kind === 'block' ? row.block.at : row.at) })
  })
  return output
}

/** 原文展示与完整复制共用消息身份和 Unicode 分页边界。 */
export function validConversationPart(part: ConversationMessagePart, sessionId: string, seq: number, start: number,
  role: ConversationMessagePart['role'], total?: number): boolean {
  return Boolean(part && part.session_id === sessionId && Number.isSafeInteger(seq) && seq >= 0 && Number.isSafeInteger(start) && start >= 0
    && part.source_seq === seq && part.role === role && part.start === start
    && Number.isSafeInteger(part.end) && Number.isSafeInteger(part.total_chars) && part.end > start
    && part.end <= part.total_chars && typeof part.text === 'string' && Array.from(part.text).length === part.end - start
    && (total === undefined || part.total_chars === total)
    && part.next_start === (part.end < part.total_chars ? part.end : null)
    && (start === 0 ? part.previous_start === null
      : Number.isSafeInteger(part.previous_start) && part.previous_start! >= 0 && part.previous_start! < start))
}

/** Copy the entire immutable reply, never a truncated preview or the hidden process. */
export async function readReply(sessionId: string, seq: number, read: (start: number) => Promise<ConversationMessagePart>): Promise<string> {
  const parts: string[] = []
  let start = 0, total: number | undefined
  while (true) {
    const part = await read(start)
    if (!validConversationPart(part, sessionId, seq, start, 'assistant', total)) throw new ResourceError({key: "ui.lib.turnCompletion.cf5425f62d93"})
    total = part.total_chars
    parts.push(part.text)
    if (part.next_start === null) return parts.join('')
    start = part.next_start
  }
}
