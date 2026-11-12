import type { SessionSummary } from '@/types'

/** Empty metadata explicitly omits reasoning; absent metadata inherits config. */
export function reasoningSelection(session: SessionSummary | null | undefined, defaultEffort: string | null) {
  const override = session?.metadata?.reasoning_effort != null || Boolean(session?.reasoning_effort)
  return { value: override ? session?.reasoning_effort || null : defaultEffort, inherited: !override }
}
