/** Share one accepted operation until its write and readback have both settled. */
export function createInflightOperations() {
  const pending = new Map<string, Promise<void>>()
  return (key: string, operation: () => Promise<void>): Promise<void> => {
    const current = pending.get(key)
    if (current) return current
    const result = Promise.resolve().then(operation).finally(() => { pending.delete(key) })
    pending.set(key, result)
    return result
  }
}
