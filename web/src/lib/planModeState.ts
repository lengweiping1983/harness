export interface ConfirmedPlanMode { active: boolean; pending: boolean | null }
export function readPlanModeState(value: unknown): ConfirmedPlanMode | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  if (typeof raw.active !== 'boolean' || raw.known === false) return null
  if (raw.pending !== undefined && raw.pending !== null && typeof raw.pending !== 'boolean') return null
  return { active: raw.active, pending: typeof raw.pending === 'boolean' && raw.pending !== raw.active ? raw.pending : null }
}
export const effectivePlanMode = (state: ConfirmedPlanMode) => state.pending ?? state.active
