import { t } from '@/i18n'
import type { Block } from '@/types'

export interface DockMember {
  key: string
  id?: string
  name: string
  role?: string
  leadAgent?: string
  leadName?: string
  status: string
  historical?: boolean
  group?: string
  agent?: string
  contextSessionId?: string
  preview?: { text: string; truncated: boolean }
}

export function memberStatus(value: string): string {
  if (value === 'completed') return 'done'
  return ['pending', 'running', 'done', 'failed', 'blocked', 'interrupted'].includes(value) ? value : 'unknown'
}

/** 合并最新运行快照；追加指令时保留仍在执行的团队，清掉旧的完成记录。 */
export function dockMembers(blocks: readonly Block[], includeHistory = false): DockMember[] {
  let start = 0
  for (let index = blocks.length - 1; index >= 0; index--) {
    const block = blocks[index]
    if (block?.kind === 'user' && !block.steeringStatus) { start = index + 1; break }
  }
  const teams = new Map<string, { block: Extract<Block, { kind: 'team' }>; index: number }>()
  const agents = new Map<string, { block: Extract<Block, { kind: 'subagent' }>; index: number }>()
  for (let index = 0; index < blocks.length; index++) {
    const block = blocks[index]!
    if (block.kind === 'team') teams.set(block.runId, { block, index })
    else if (block.kind === 'subagent') agents.set(block.agentId, { block, index })
  }
  const rows: DockMember[] = []
  const represented = new Set<string>()
  for (const { block: team, index } of teams.values()) {
    const visible = index >= start || !['done', 'failed', 'interrupted'].includes(team.phase) && team.members.some(member => ['pending', 'running', 'blocked'].includes(memberStatus(member.status)))
    for (const member of team.members) {
      const id = member.agent_id || member.planning_agent_id
      if (member.agent_id) represented.add(member.agent_id)
      if (member.planning_agent_id) represented.add(member.planning_agent_id)
      if (member.role === 'lead' || (!visible && !includeHistory) || !id) continue
      rows.push({ key: JSON.stringify([team.runId, member.name]), id, name: member.displayName || member.name,
        role: member.role, leadAgent: team.members.find(row => row.role === 'lead')?.agent, leadName: team.members.find(row => row.role === 'lead')?.displayName || team.members.find(row => row.role === 'lead')?.name, historical: !visible, group: team.task || team.displayName || team.team, status: memberStatus(member.status), agent: member.agent, contextSessionId: member.context_session_id, preview: team.previews?.[member.name] })
    }
  }
  for (const { block: agent, index } of agents.values()) {
    const historical = index < start && !['pending', 'running'].includes(memberStatus(agent.status))
    if (represented.has(agent.agentId) || historical && !includeHistory) continue
    rows.push({ key: agent.agentId, id: agent.agentId, name: agent.description || agent.agentId, historical, status: memberStatus(agent.status),
      preview: agent.result ? { text: agent.result, truncated: false } : undefined })
  }
  return rows
}

/** 按身份、回合和正文建立索引，避免每次流更新都重复扫描整段历史。 */
function replyIndex(blocks: readonly Block[]) {
  const authors = new Map<string, number>()
  const texts = new Map<string, number>()
  const leadSummaries = new Map<string, number>()
  const leadTexts = new Map<string, number>()
  const remember = (index: Map<string, number>, key: string, at: number) => index.set(key, Math.max(index.get(key) ?? -Infinity, at))
  for (const block of blocks) {
    if (block.kind !== 'assistant' || !block.author) continue
    const {team, member, role} = block.author
    const scope = [team, member, block.turn]
    const text = block.text.trim()
    if (!block.streaming) remember(authors, JSON.stringify(scope), block.at)
    remember(texts, JSON.stringify([...scope, text]), block.at)
    if (role === 'lead') {
      remember(leadSummaries, JSON.stringify([team, block.turn, text]), block.at)
      remember(leadTexts, JSON.stringify([...scope, block.text.trim()]), block.at)
    }
  }
  return {authors, texts, leadSummaries, leadTexts}
}

/** 对话保留主 Agent 的交付和重要团队记录，自动调度的成员过程放到右侧，直接点名的回复保留在主会话。 */
export function mainConversationBlocks(blocks: Block[], leadConversation?: { runId: string; blocks: Block[] } | null): Block[] {
  const original = replyIndex(blocks)
  const visible = blocks.flatMap((block): Block[] => {
    if (block.kind === 'subagent' || block.kind === 'assistant' && block.author?.stage === 'member' && block.author.role !== 'lead') return []
    if (block.kind !== 'team') return [block]
    const summary = block.summary?.trim()
    const duplicate = summary && (block.publicReply && block.publicReply.text.trim() === summary || (original.leadSummaries.get(JSON.stringify([block.team, block.turn, summary])) ?? -Infinity) >= block.at)
    const presented = duplicate || block.direct ? { ...block, summary: undefined } : block
    const result: Block[] = []
    const lead = block.members.find(member => member.role === 'lead')
    if (leadConversation?.runId === block.runId) result.push(...leadConversation.blocks.filter(row => row.kind !== 'assistant' || (original.leadTexts.get(JSON.stringify([row.author?.team, row.author?.member, block.turn, row.text.trim()])) ?? -Infinity) < block.at))
    if (!block.direct && lead && Object.keys(block.assignments ?? {}).length) {
      const assignments = Object.entries(block.assignments ?? {}).map(([name, task]) => {
        const member = block.members.find(row => row.name === name)
        return t('ui.team.assignment', {name: member?.displayName || name, status: t('ui.member.status.' + memberStatus(member?.status || 'pending')), task: task.length > 140 ? task.slice(0, 140) + '…' : task})
      })
      result.push({ id: 'team-plan:' + block.runId, kind: 'assistant', at: block.at, turn: block.turn,
        text: (block.decisionReason?.trim() ? block.decisionReason + '\n\n' : '') + assignments.join('\n'),
        author: { team: block.team, member: lead.name, display_name: lead.displayName || lead.name, role: 'lead', stage: 'reply', agent: lead.agent } })
    }
    if (presented.summary || presented.error || presented.worktrees?.length) result.push(presented)
    return result
  })
  const projected = replyIndex(visible)
  const live: Block[] = []
  for (const block of blocks) {
    if (block.kind !== 'team') continue
    const terminal = ['done', 'failed', 'interrupted'].includes(block.phase)
    if (block.direct) {
      for (const member of block.members) {
        const preview = block.previews?.[member.name]
        if (preview?.stage !== 'reply' || preview.status !== 'streaming' || !preview.text) continue
        if ((projected.authors.get(JSON.stringify([block.team, member.name, block.turn])) ?? -Infinity) >= block.at) continue
        live.push({ id: 'member-stream:' + JSON.stringify([block.runId, member.name, preview.requestId]), kind: 'assistant', at: block.at, turn: block.turn, text: preview.text, streaming: !terminal, previewOnly: terminal, partial: block.phase === 'failed' ? 'failed' : block.phase === 'interrupted' ? 'stopped' : undefined, previewTruncated: preview.truncated,
          author: { team: block.team, member: member.name, display_name: member.displayName || member.name, role: member.role === 'lead' ? 'lead' : 'member', stage: 'reply', agent: member.agent } })
      }
      continue
    }
    const lead = block.members.find(member => member.role === 'lead')
    const active = !terminal && lead && block.previews?.[lead.name]
    const preview = active && active.status === 'streaming' && active.text && ['reply', 'summary'].includes(active.stage ?? '') ? active : block.publicReply
    const author = lead || (block.publicReply ? { name: block.publicReply.member, displayName: block.publicReply.displayName, agent: block.publicReply.agent } : null)
    if (!author || !preview?.text || !['reply', 'summary'].includes(preview.stage ?? '')) continue
    const text = preview.text
    if ((projected.texts.get(JSON.stringify([block.team, author.name, block.turn, text.trim()])) ?? -Infinity) >= block.at) continue
    live.push({ id: 'lead-stream:' + JSON.stringify([block.runId, preview.requestId]), kind: 'assistant', at: block.at, turn: block.turn,
      text, streaming: Boolean(!terminal && active && active.status === 'streaming' && active.text), partial: block.phase === 'failed' ? 'failed' : block.phase === 'interrupted' ? 'interrupted' : undefined, previewTruncated: preview.truncated,
      author: { team: block.team, member: author.name, display_name: author.displayName || author.name, role: 'lead', stage: preview.stage as 'reply' | 'summary', ...(author.agent ? { agent: author.agent } : {}) } })
  }
  return [...visible, ...live]

}

export function dockStatus(members: readonly DockMember[]): string | null {
  return ['failed', 'blocked', 'interrupted', 'running', 'pending', 'unknown', 'done'].find(status => members.some(member => member.status === status)) ?? null
}
