import type { Block, ToolBlockState } from '@/types'
import { getFileRoot } from './fileRoot'
import { relativizeToRoot } from './deliverables'

export interface ChangedFile { path: string; added?: number; removed?: number; operations: number }
export type ChangeRow = { kind: 'block'; id: string; block: Block } | { kind: 'changes'; id: string; files: ChangedFile[]; at: number; turn?: number; pending: boolean }

/** 只汇总成功完成的文件工具；命令和原有 Git 改动不能据此归到 Agent 名下。 */
export function fileChangeRows(blocks: Block[], running: boolean): ChangeRow[] {
  const rows: ChangeRow[] = []
  const groups = new Map<number | undefined, { files: Map<string, ChangedFile>; calls: Set<string> }>()
  const last = new Map<number, number>()
  const ended = new Set<number>()
  const turnOf = (block: Block) => Number.isSafeInteger(block.turn) && block.turn! > 0 ? block.turn : undefined
  blocks.forEach((block, index) => {
    const turn = turnOf(block)
    if (turn === undefined) return
    last.set(turn, index)
    if (block.kind === 'turn' && block.phase === 'end') ended.add(turn)
  })
  const flush = (turn: number | undefined, id: string, at: number, pending = false) => {
    const group = groups.get(turn)
    if (group?.files.size) rows.push({ kind: 'changes', id: turn ? `changes-turn-${turn}` : `changes-${id}`, files: [...group.files.values()], at, turn, pending })
    groups.delete(turn)
  }
  for (const [index, block] of blocks.entries()) {
    const turn = turnOf(block)
    if (turn !== undefined || block.kind === 'user') flush(undefined, block.id, block.at)
    rows.push({ kind: 'block', id: block.id, block })
    let group = groups.get(turn)
    if (!group) {
      group = { files: new Map(), calls: new Set() }
      groups.set(turn, group)
    }
    const { files, calls } = group
    const callKey = block.kind === 'tool' ? block.sourceSeq != null ? `seq:${block.sourceSeq}` : `call:${block.callId}` : null
    if (block.kind === 'tool' && block.status === 'ok' && !calls.has(callKey!)) {
      calls.add(callKey!)
      const change = changedFile(block)
      if (change) {
        const path = relativizeToRoot(change.path, getFileRoot())
        const key = /^[A-Za-z]:/.test(getFileRoot() ?? path) ? path.toLowerCase() : path
        const previous = files.get(key)
        files.set(key, { ...change, path, operations: (previous?.operations ?? 0) + 1,
          added: previous ? previous.added !== undefined && change.added !== undefined ? previous.added + change.added : undefined : change.added,
          removed: previous ? previous.removed !== undefined && change.removed !== undefined ? previous.removed + change.removed : undefined : change.removed })
      }
    }
    // 同一回合只在最后一条记录后汇总；错误提示和交错事件不能生成重复虚拟行键。
    if (turn !== undefined && last.get(turn) === index) flush(turn, block.id, block.at, running && !ended.has(turn))
    else if (turn === undefined && block.kind === 'turn' && block.phase === 'end') flush(undefined, block.id, block.at)
  }
  flush(undefined, blocks.at(-1)?.id ?? 'empty', blocks.at(-1)?.at ?? 0, running)
  return rows
}

export function changedFile(block: ToolBlockState): ChangedFile | null {
  const input = block.input as Record<string, unknown> | undefined
  const display = block.display as Record<string, unknown> | undefined
  if (!['write', 'edit'].includes(block.name)) return null
  const path = display?.path ?? input?.path
  if (typeof path !== 'string' || !path.trim()) return null
  const diff = typeof display?.diff === 'string' ? display.diff : null
  if (display?.changed === false || diff === '' && display?.changed !== true) return null
  let added = 0, removed = 0, inHunk = false
  for (const line of diff?.split('\n') ?? []) {
    if (line.startsWith('@@')) { inHunk = true; continue }
    if (inHunk && line.startsWith('+')) added += 1
    if (inHunk && line.startsWith('-')) removed += 1
  }
  const known = diff !== null && display?.diff_truncated !== true
  return { path, operations: 1, added: known ? added : undefined, removed: known ? removed : undefined }
}
