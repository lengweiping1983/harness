
export interface ComposerAgent {
  name: string
  description: string
  display_name?: string
  profession?: string
  agent_type: 'agent' | 'team'
  enabled: boolean
  members?: { name: string }[]
}

export function filterComposerAgents(agents: ComposerAgent[], query: string, kind: 'all' | 'agent' | 'team'): ComposerAgent[] {
  const needle = query.trim().toLocaleLowerCase()
  return agents.filter(agent => agent.enabled !== false && (kind === 'all' || agent.agent_type === kind)
    && `${agent.name} ${agent.display_name ?? ''} ${agent.profession ?? ''} ${agent.description}`.toLocaleLowerCase().includes(needle))
}
