/** Keep unpersisted window edits authoritative over older browser storage. */
export function createWindowDraftStorage() {
  const memory = new Map<string, string | null>()
  const pending = new Set<string>()
  return {
    unpersisted: (id: string) => pending.has(id),
    read: (id: string): string | null => {
      if (pending.has(id)) return memory.get(id) ?? null
      try {
        const raw = sessionStorage.getItem(id)
        memory.set(id, raw)
        return raw
      } catch { return memory.get(id) ?? null }
    },
    write: (id: string, raw: string | null, persist = true): boolean => {
      memory.set(id, raw)
      pending.add(id)
      if (!persist) return false
      try {
        if (raw === null) sessionStorage.removeItem(id)
        else sessionStorage.setItem(id, raw)
        pending.delete(id)
        return true
      } catch { return false }
    },
  }
}
