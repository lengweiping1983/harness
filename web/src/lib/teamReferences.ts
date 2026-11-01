type CatalogAgent = { name: string; agent_type: 'agent' | 'team'; enabled?: boolean }
type Member = { name: string; agent?: string | null }
export type TeamReferenceIssue = { member: string; agent: string; reason: 'missing' | 'disabled' | 'team' }

/** Use the complete scoped catalog, even when the visible list is filtered. */
export function teamReferenceIssues(members: Member[] | undefined, catalog: CatalogAgent[]): TeamReferenceIssue[] {
  const agents = new Map(catalog.map(agent => [agent.name, agent]))
  return (members ?? []).flatMap(member => {
    if (!member.agent) return []
    const referenced = agents.get(member.agent)
    const reason = !referenced ? 'missing' : referenced.enabled === false ? 'disabled' : referenced.agent_type === 'team' ? 'team' : null
    return reason ? [{ member: member.name, agent: member.agent, reason }] : []
  })
}
