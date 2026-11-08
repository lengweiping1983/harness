import type {SessionActivity, SessionCompletion} from './protocol.generated'

export function createCompletionObserver(deliver: (completion: SessionCompletion) => void, gap: () => void) {
  let cursor: {serverId: string; after: number} | undefined
  const handled = new Set<string>()
  const live = (row: SessionCompletion) => {
    const key = JSON.stringify([row.session_id, row.run_id])
    if (handled.has(key)) return
    handled.add(key)
    if (handled.size > 1200) handled.delete(handled.values().next().value!)
    deliver(row)
  }
  return {
    cursor: () => cursor,
    live,
    receive(snapshot: SessionActivity) {
      const previous = cursor
      if (previous?.serverId === snapshot.server_id && snapshot.completion_cursor <= previous.after) return
      if (previous?.serverId === snapshot.server_id) {
        let after = previous.after
        let invalidSequence = false
        for (const row of snapshot.completions) {
          if (!Number.isSafeInteger(row.seq) || row.seq <= after || row.seq > snapshot.completion_cursor) {
            invalidSequence = true
            break
          }
          after = row.seq
        }
        if (invalidSequence) {
          gap()
          cursor = {serverId: snapshot.server_id, after: snapshot.completion_cursor}
          return
        }
      }
      if (previous && previous.serverId !== snapshot.server_id) handled.clear()
      cursor = {serverId: snapshot.server_id, after: snapshot.completion_cursor}
      if (!previous || previous.serverId !== snapshot.server_id) return
      if (snapshot.completions_lost) gap()
      for (const row of snapshot.completions) if (row.seq > previous.after) live(row)
    },
  }
}