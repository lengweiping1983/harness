import type { Block, ConversationHistory } from '@/types'

export type MessageNavigationEntry = { id: string; index: number; text: string; sourceSeq?: number }

export function messageNavigationEntries(rows: { kind: string; id: string; block?: Block }[], history?: ConversationHistory | null): MessageNavigationEntry[] {
  const loaded = rows.flatMap((row, index) => row.block?.kind === 'user'
    ? [{ id: row.id, index, text: row.block.text, sourceSeq: row.block.sourceSeq }] : [])
  const bySeq = new Map(loaded.filter(entry => entry.sourceSeq !== undefined).map(entry => [entry.sourceSeq, entry]))
  const indexed = (history?.user_messages ?? []).map(message => bySeq.get(message.source_seq) ?? {
    id: 'navigation-user:' + message.source_seq, index: -1, text: message.text, sourceSeq: message.source_seq,
  })
  const known = new Set(indexed.map(entry => entry.sourceSeq))
  return [...indexed, ...loaded.filter(entry => entry.sourceSeq === undefined || !known.has(entry.sourceSeq))]
}
