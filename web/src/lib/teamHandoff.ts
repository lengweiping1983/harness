import type { TeamMemberState } from '@/types'

const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value))
const hash = (value: unknown): value is string => typeof value === 'string' && /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(value)

export function readTeamHandoff(row: Record<string, unknown>): Pick<TeamMemberState, 'delivery' | 'delivery_inputs' | 'handoff_error'> {
  const result: Pick<TeamMemberState, 'delivery' | 'delivery_inputs' | 'handoff_error'> = {}
  if (record(row.delivery) && hash(row.delivery.tree)) {
    result.delivery = {tree: row.delivery.tree, ...(hash(row.delivery.commit) ? {commit: row.delivery.commit} : {})}
  }
  if (record(row.delivery_inputs) && hash(row.delivery_inputs.tree) && Array.isArray(row.delivery_inputs.sources)) {
    const sources = row.delivery_inputs.sources.filter(record).filter(source => typeof source.id === 'string' && Boolean(source.id) && hash(source.tree)).map(source => ({id: source.id as string, tree: source.tree as string}))
    result.delivery_inputs = {tree: row.delivery_inputs.tree, sources: sources.filter((source, index) => sources.findIndex(other => other.id === source.id) === index)}
  }
  if (record(row.handoff_error) && typeof row.handoff_error.message === 'string' && row.handoff_error.message) {
    const details = row.handoff_error.details
    result.handoff_error = {message: row.handoff_error.message,
      ...(typeof row.handoff_error.code === 'string' ? {code: row.handoff_error.code} : {}),
      conflicts: record(details) && Array.isArray(details.conflicts) ? details.conflicts.filter((path): path is string => typeof path === 'string') : []}
  }
  return result
}
