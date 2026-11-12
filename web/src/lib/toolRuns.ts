import { t } from '@/i18n'
import type { Block } from '@/types'
import type { ConversationRow } from './turnActivity'
import { toolActivity, toolActivityKind, type ActivityKind } from './toolActivity'
import {authoredPlugin} from './pluginReview'

export interface ToolRunRow {
  kind: 'tool-run'
  activityKind: ActivityKind
  id: string
  at: number
  turn?: number
  open: boolean
  summary: string
  label: string
  thoughts: number
  count: number
  running: number
  failed: number
  skipped: number
  background: number
}
export type GroupedConversationRow = ConversationRow | ToolRunRow
const ACTIONS: Record<ActivityKind, string> = {
  get read() { return t("ui.lib.toolRuns.407dd16f938f") }, get edit() { return t("ui.lib.toolRuns.a6a7e454cf11") }, get search() { return t("ui.lib.toolRuns.44ce7ae909bb") }, get image() { return t("ui.lib.toolRuns.a9769c635970") }, get command() { return t("ui.lib.toolRuns.09fcdbfd066f") },
  get web() { return t("ui.lib.toolRuns.1846b9b97b84") }, get agent() { return t("ui.lib.toolRuns.45385d778dc3") }, get plan() { return t("ui.lib.toolRuns.3e3a37e9416c") }, get skill() { return t("ui.lib.toolRuns.11ff0491f783") }, get tool() { return t("ui.lib.toolRuns.1c31ae359a79") },
}

/** Group adjacent process blocks from the same turn; preserve narrative and author boundaries. */
function eligible(block: Block): boolean {
  return (block.kind === 'thinking' && block.turn !== undefined && !block.author && !block.speaker) ||
    (block.kind === 'tool' && block.name !== 'ask_user_question' && block.name !== 'show_widget' && !authoredPlugin(block))
}
export function groupToolRuns(rows: ConversationRow[], choices: Record<string, boolean>, identities = new Map<string, string>()): GroupedConversationRow[] {
  const result: GroupedConversationRow[] = []
  const used = new Set<string>()
  let start = 0
  while (start < rows.length) {
    const first = rows[start]!
    if (first.kind !== 'block' || !eligible(first.block)) { result.push(first); start++; continue }
    let end = start + 1
    while (end < rows.length) {
      const next = rows[end]!
      if (next.kind !== 'block' || !eligible(next.block) || next.block.turn !== first.block.turn) break
      end++
    }
    if (end - start < 2) { result.push(first); start = end; continue }
    const counts = new Map<ActivityKind, number>()
    let running = 0, failed = 0, skipped = 0, background = 0, thoughts = 0
    for (let index = start; index < end; index++) {
      const block = (rows[index] as { kind: 'block'; block: Block }).block
      if (block.kind === 'thinking') {
        thoughts++
        if (block.streaming) running++
        continue
      }
      if (block.kind !== 'tool') continue
      const kind = toolActivityKind(block)
      counts.set(kind, (counts.get(kind) ?? 0) + 1)
      const display = block.display && typeof block.display === 'object' ? block.display as Record<string, unknown> : {}
      if (block.status === 'running') running++
      else if (display.not_executed || display.steering_deferred) skipped++
      else if (block.status === 'error') failed++
      else if (display.background) background++
    }
    let id = ''
    for (let index = start; index < end; index++) {
      const previous = identities.get(rows[index]!.id)
      if (previous && !used.has(previous)) { id = previous; break }
    }
    id ||= `tool-run:${first.block.turn ?? 'unknown'}:${first.id}`
    used.add(id)
    for (let index = start; index < end; index++) identities.set(rows[index]!.id, id)
    const open = choices[id] ?? false
    const latest = (rows[end - 1] as Extract<ConversationRow, {kind: 'block'}>).block
    const label = latest.kind === 'thinking' && latest.streaming ? t('ui.process.thinking')
      : thoughts === end - start ? t('ui.process.thoughtRecord')
      : latest.kind === 'tool' ? toolActivity(latest).label : t('ui.process.activity')
    result.push({ kind: 'tool-run', activityKind: counts.size === 1 ? counts.keys().next().value! : counts.has('search') || counts.has('read') ? 'search' : counts.has('command') ? 'command' : 'tool', id, at: first.block.at, turn: first.block.turn, open, count: end - start,
      label, thoughts,
      summary: thoughts ? t(end - start === thoughts ? 'ui.process.thoughts' : 'ui.process.summary', { thoughts, tools: end - start - thoughts }) : [...counts].map(([kind, count]) => t("ui.lib.toolRuns.e496f90b2f3d", {p0: (ACTIONS[kind]), p1: (count)})).join(' · '), running, failed, skipped, background })
    for (let index = start; index < end; index++) {
      const row = rows[index] as Extract<ConversationRow, {kind: 'block'}>
      // A summary cannot replace ongoing work or an actionable failure.
      if (open || (row.block.kind === 'tool' && row.block.status !== 'ok')) result.push(row)
    }
    start = end
  }
  return result
}
