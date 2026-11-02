import type { SessionEvent } from '@/types'
import type { ChangeRow } from './fileChanges'
import {hasVisualization} from './visualizationContent'
import {authoredPlugin} from './pluginReview'

export interface TurnActivity {
  turn: number
  runId?: string
  startedAt?: number
  startSeq?: number
  endedAt?: number
  endSeq?: number
  reason?: string
  finalResponseSeq?: number
}

/** 只用轮次事件计算时长；缺失、损坏或逆序的边界不能伪装成零耗时。 */
export function turnElapsed(activity: TurnActivity, now?: number): number | null {
  const start = activity.startedAt, end = activity.endedAt ?? now
  if (typeof start !== 'number' || !Number.isFinite(start) || start < 0 ||
      typeof end !== 'number' || !Number.isFinite(end) || end < start) return null
  return end - start
}

export function mergeTurnActivity(previous: Record<number, TurnActivity>, events: SessionEvent[]): Record<number, TurnActivity> {
  const next = { ...previous }
  for (const event of events) {
    if (!['turn/start', 'turn/end'].includes(event.type)) continue
    const data = event.data ?? {}, turn = data.turn
    if (typeof turn !== 'number' || !Number.isSafeInteger(turn) || turn < 1) continue
    const old = next[turn] ?? { turn }
    if (event.type === 'turn/start') {
      next[turn] = { ...old, runId: typeof data.run_id === 'string' ? data.run_id : old.runId, startedAt: event.time ?? old.startedAt, startSeq: event.seq ?? old.startSeq }
    } else {
      if (old.endSeq !== undefined && event.seq !== undefined && event.seq < old.endSeq) continue
      const reason = data.reason && typeof data.reason === 'object' ? (data.reason as Record<string, unknown>).kind : data.reason
      next[turn] = { ...old, endedAt: event.time ?? old.endedAt, endSeq: event.seq ?? old.endSeq,
        reason: typeof reason === 'string' ? reason : old.reason,
        finalResponseSeq: typeof data.final_response_seq === 'number' ? data.final_response_seq : old.finalResponseSeq }
    }
  }
  return next
}

export type ActivityRow = { kind: 'activity'; id: string; at: number; turn: number; activity: TurnActivity; open: boolean; count: number; partial: boolean }
export type ConversationRow = ChangeRow | ActivityRow

/** Flatten expanded process rows into the existing virtual list, never an inner DOM pile. */
export function conversationRows(rows: ChangeRow[], turns: Record<number, TurnActivity>, choices: Record<number, boolean>): ConversationRow[] {
  const groups = new Map<number, { count: number; first: number; at: number; partial: boolean }>()
  const userTurns = new Set(rows.flatMap(row => row.kind === 'block' && row.block.kind === 'user' && row.block.turn &&
    (row.block.sourceSeq === undefined || row.block.sourceSeq === (turns[row.block.turn]?.startSeq ?? -2) + 1) ? [row.block.turn] : []))
  const process = (row: ChangeRow) => {
    if (row.kind !== 'block' || !row.block.turn || !turns[row.block.turn]) return false
    const block = row.block, activity = turns[block.turn!]!
    if (block.kind === 'user' || block.kind === 'error' || block.kind === 'question' || block.kind === 'deliverables' || block.kind === 'turn') return false
    if (block.kind === 'tool' && (block.status !== 'ok' || block.name === 'ask_user_question' || block.name === 'show_widget' || authoredPlugin(block))) return false
    if (block.kind === 'notice' && block.tone === 'warn') return false
    if (block.kind === 'subagent' && block.status !== 'completed') return false
    // 未收到最终回复关联时保留正文可见，避免运行中或不完整窗口隐藏回复。
    if (block.kind === 'assistant' && (block.author || activity.finalResponseSeq === undefined || block.sourceSeq === activity.finalResponseSeq || block.partial || hasVisualization(block))) return false
    return true
  }
  for (const [index, row] of rows.entries()) {
    if (!process(row) || row.kind !== 'block') continue
    const turn = row.block.turn!, old = groups.get(turn)
    if (old) old.count++
    else {
      const activity = turns[turn]!
      groups.set(turn, { count: 1, first: index, at: row.block.at, partial: !userTurns.has(turn) || activity.startSeq === undefined })
    }
  }
  // 即使尚无思考或工具，也为已加载的轮次显示计时；不借用消息时间猜测起点。
  for (const [index, row] of rows.entries()) {
    const turn = row.kind === 'block' ? row.block.turn : row.turn
    if (!turn || !turns[turn]) continue
    const group = groups.get(turn)
    const first = row.kind === 'block' && row.block.kind === 'user' ? index + 1 : index
    const at = row.kind === 'block' ? row.block.at : row.at
    if (!group) groups.set(turn, { count: 0, first, at, partial: !userTurns.has(turn) || turns[turn]!.startSeq === undefined })
    else if (first < group.first) { group.first = first; group.at = at }
  }
  const output: ConversationRow[] = []
  const headers = new Map<number, ActivityRow[]>()
  for (const [turn, group] of groups) {
    const activity = turns[turn]!
    const open = choices[turn] ?? activity.reason !== 'completed'
    const header: ActivityRow = { kind: 'activity', id: `activity-turn-${turn}`, at: group.at,
      turn, activity, open, count: group.count, partial: group.partial }
    headers.set(group.first, [...(headers.get(group.first) ?? []), header])
  }
  for (let index = 0; index <= rows.length; index++) {
    output.push(...(headers.get(index) ?? []))
    const row = rows[index]
    if (!row) continue
    const turn = row.kind === 'block' ? row.block.turn : row.turn
    const activity = turn ? turns[turn] : undefined
    const open = turn !== undefined && (choices[turn] ?? activity?.reason !== 'completed')
    if (!process(row) || open) {
      output.push(row)
    }
  }
  return output
}
