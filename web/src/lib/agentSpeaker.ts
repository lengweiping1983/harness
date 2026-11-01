export interface AgentSpeaker {
  agent: string
  display_name: string
  profession?: string
}

/** 历史使用服务端保存的身份；缺失或畸形数据不借用当前选中的 Agent。 */
export function readAgentSpeaker(value: unknown): AgentSpeaker | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return
  const data = value as Record<string, unknown>
  if (typeof data.agent !== 'string' || !data.agent.trim() || typeof data.display_name !== 'string' || !data.display_name.trim()) return
  return {agent: data.agent, display_name: data.display_name,
    ...(typeof data.profession === 'string' && data.profession.trim() ? {profession: data.profession} : {})}
}

export function agentSpeakerAvatar(speaker: AgentSpeaker, sessionId?: string | null): string {
  return '/api/v1/plugins/agents/' + encodeURIComponent(speaker.agent) + '/avatar' + (sessionId ? '?session_id=' + encodeURIComponent(sessionId) : '')
}
