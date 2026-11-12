import {t} from '@/i18n'
import {noticeText} from './noticeText'
import type { RuntimeSubagent } from '@/types'

/** IDs are never reused: a settled task cannot become live in a delayed page. */
export function mergeAgents(previous: RuntimeSubagent[], current: RuntimeSubagent[]): RuntimeSubagent[] {
  const rows = new Map(previous.map((agent) => [agent.agent_id, agent]))
  const finished = (agent: RuntimeSubagent) => ['completed', 'failed', 'interrupted'].includes(agent.status)
  for (const agent of current) {
    const known = rows.get(agent.agent_id)
    if (!known || !finished(known) || finished(agent)) rows.set(agent.agent_id, agent)
  }
  return [...rows.values()].sort((a, b) => b.created_at - a.created_at || (a.agent_id < b.agent_id ? 1 : a.agent_id > b.agent_id ? -1 : 0))
}

export interface AgentTreeRow {
  agent: RuntimeSubagent
  depth: number
  parentDescription?: string
  missingParent: boolean
  brokenRelation: boolean
  descendants: number
  activeDescendants: number
}

/** A paged forest, not a claim that every ancestor or historical child is loaded.
 * Iterative traversal also tolerates corrupted cycles and very deep histories. */
export function agentTree(agents: RuntimeSubagent[], collapsed: ReadonlySet<string> = new Set()): AgentTreeRow[] {
  const ordered = mergeAgents([], agents)
  const byId = new Map(ordered.map(agent => [agent.agent_id, agent]))
  const children = new Map<string, RuntimeSubagent[]>()
  for (const agent of ordered) {
    if (agent.parent_agent_id && byId.has(agent.parent_agent_id)) {
      const siblings = children.get(agent.parent_agent_id) ?? []
      siblings.push(agent)
      children.set(agent.parent_agent_id, siblings)
    }
  }
  const roots = ordered.filter(agent => !agent.parent_agent_id || !byId.has(agent.parent_agent_id))
  const rows: (AgentTreeRow & { parent: number | null; hidden: boolean })[] = []
  const seen = new Set<string>()
  for (const root of [...roots, ...ordered]) {
    if (seen.has(root.agent_id)) continue
    const stack: { agent: RuntimeSubagent; depth: number; parent: number | null }[] = [{ agent: root, depth: 0, parent: null }]
    while (stack.length) {
      const { agent, depth, parent } = stack.pop()!
      if (seen.has(agent.agent_id)) continue
      seen.add(agent.agent_id)
      const index = rows.length
      rows.push({ agent, depth, parent, hidden: parent !== null && (rows[parent].hidden || collapsed.has(rows[parent].agent.agent_id)),
        parentDescription: agent.parent_agent_id ? byId.get(agent.parent_agent_id)?.description : undefined,
        missingParent: !!agent.parent_agent_id && !byId.has(agent.parent_agent_id),
        brokenRelation: parent === null && !!agent.parent_agent_id && byId.has(agent.parent_agent_id),
        descendants: 0, activeDescendants: 0 })
      const nested = children.get(agent.agent_id) ?? []
      for (let i = nested.length - 1; i >= 0; i--) stack.push({ agent: nested[i], depth: depth + 1, parent: index })
    }
  }
  for (let i = rows.length - 1; i >= 0; i--) {
    const row = rows[i]
    if (row.parent !== null) {
      rows[row.parent].descendants += row.descendants + 1
      rows[row.parent].activeDescendants += row.activeDescendants + (['pending', 'running'].includes(row.agent.status) ? 1 : 0)
    }
  }
  return rows.filter(row => !row.hidden)
}

export type AgentView = 'all' | 'active' | 'attention'

export function agentNeedsAttention(agent: RuntimeSubagent): boolean {
  return agent.status === 'failed' || agent.status === 'interrupted' || !!agent.error || !!agent.archive_error
}

/** Keep ancestors as context without hiding matching descendants in collapsed branches. */
export function filterAgentTree(rows: AgentTreeRow[], view: AgentView, query: string): AgentTreeRow[] {
  const needle = query.trim().toLocaleLowerCase()
  if (view === 'all' && !needle) return rows
  const included = new Set<string>()
  const byId = new Map(rows.map(row => [row.agent.agent_id, row]))
  for (const row of rows) {
    const agent = row.agent
    const matchesStatus = view === 'all' || (view === 'active'
      ? agent.status === 'pending' || agent.status === 'running' : agentNeedsAttention(agent))
    const matchesQuery = !needle || [agent.description, agent.agent_id, agentErrorText(agent), agent.archive_error_copy ? noticeText(agent.archive_error_copy) : agent.archive_error]
      .some(value => value?.toLocaleLowerCase().includes(needle))
    if (!matchesStatus || !matchesQuery) continue
    let current: AgentTreeRow | undefined = row
    while (current && !included.has(current.agent.agent_id)) {
      included.add(current.agent.agent_id)
      current = current.agent.parent_agent_id ? byId.get(current.agent.parent_agent_id) : undefined
    }
  }
  return rows.filter(row => included.has(row.agent.agent_id))
}

/** 结构化原因按当前语言呈现，外部诊断及机器标签保持原样。 */
export function agentErrorText(agent: Pick<RuntimeSubagent, 'error' | 'error_copy' | 'error_parts'>): string {
  if (agent.error_parts?.length) return agent.error_parts.map(part => t('ui.agent.errorDetail', {label: part.label, message: noticeText(part.message)})).join(t('ui.agent.errorSeparator'))
  return agent.error_copy ? noticeText(agent.error_copy) : agent.error ?? ''
}
