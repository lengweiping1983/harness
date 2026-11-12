import type { Block } from '@/types'
import { changedFile } from './fileChanges'

export interface ChangeReviewTurn { turn: number; summary: string; hasChanges: boolean }

/** 只用显式轮次关联任务；普通问答不能取代最近有文件证据的轮次。 */
export function changeReviewTurns(blocks: Block[]): ChangeReviewTurn[] {
  const turns = new Map<number, ChangeReviewTurn>()
  for (const block of blocks) {
    if (!Number.isSafeInteger(block.turn) || block.turn! <= 0) continue
    const entry = turns.get(block.turn!) ?? { turn: block.turn!, summary: '', hasChanges: false }
    if (block.kind === 'user' && !entry.summary) entry.summary = block.text.trim().replace(/\s+/g, ' ').slice(0, 80)
    if (block.kind === 'tool' && block.status === 'ok' && changedFile(block)) entry.hasChanges = true
    turns.set(entry.turn, entry)
  }
  return [...turns.values()].sort((a, b) => b.turn - a.turn)
}
