/** 草稿共用内存回退；存储范围和错误提示由各自状态仓库决定。 */
export function createBrowserDraftStorage(resolve: () => Storage | undefined, failed: (reason?: 'invalid') => void, valid?: (raw: string) => boolean) {
  const memory = new Map<string, string>()
  const pending = new Set<string>()
  const check = (raw: string | null) => {
    if (raw !== null && valid && !valid(raw)) {
      failed('invalid'); return false
    }
    return true
  }
  return {
    getItem(key: string): string | null {
      try {
        const storage = resolve()
        if (storage === undefined) return memory.get(key) ?? null
        const raw = storage.getItem(key)
        if (!check(raw) || pending.has(key)) return memory.get(key) ?? null
        if (raw === null) memory.delete(key)
        else memory.set(key, raw)
        return raw
      }
      catch { failed(); return memory.get(key) ?? null }
    },
    setItem(key: string, value: string): void {
      memory.set(key, value); pending.add(key)
      try {
        const storage = resolve()
        if (storage === undefined || !check(storage.getItem(key))) return
        storage.setItem(key, value)
        pending.delete(key)
      }
      catch { failed() }
    },
    removeItem(key: string): void {
      memory.delete(key); pending.add(key)
      try {
        const storage = resolve()
        if (storage === undefined || !check(storage.getItem(key))) return
        storage.removeItem(key)
        pending.delete(key)
      }
      catch { failed() }
    },
  }
}
