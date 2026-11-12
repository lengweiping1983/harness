import type { Block, TeamBlock, TeamMemberState } from '@/types'

/** 子会话的序号与轮次只在子会话内有效，中央展示使用所属父回合。 */
export function projectLeadConversation(blocks: readonly Block[], team: Pick<TeamBlock, 'runId' | 'team' | 'turn'>, lead: Pick<TeamMemberState, 'name' | 'displayName' | 'agent'>, childTurn?: number): Block[] {
  return blocks.flatMap((block): Block[] => {
    if (childTurn != null && block.turn != null && block.turn !== childTurn) return []
    if (block.kind !== 'thinking' && block.kind !== 'assistant') return []
    const text = block.text
    if (block.kind === 'assistant' && !text.trim()) return []
    return [{...block, id: `lead-context:${team.runId}:${block.kind}:${block.sourceSeq ?? block.id}`,
      sourceSeq: undefined, turn: team.turn, text,
      author: {team: team.team, member: lead.name, display_name: lead.displayName || lead.name, role: 'lead', stage: 'reply', agent: lead.agent}}]
  })
}
