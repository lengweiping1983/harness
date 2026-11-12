import { useEffect, useState } from 'react'
import type { Block, TeamBlock } from '@/types'
import { api } from '@/lib/api'
import { usePageVisible } from './usePageVisible'
import { blocksFromDetail } from '@/store/chat'
import { projectLeadConversation } from '@/lib/leadConversation'

/** 主理人的规划会话属于主对话；仅读取实际保存的思考与自然语言发言。 */
export function useLeadConversation(owner: string | null, blocks: readonly Block[]): { runId: string; blocks: Block[] } | null {
  const pageVisible = usePageVisible()
  const team = [...blocks].reverse().find((row): row is TeamBlock => row.kind === 'team')
  const lead = team?.members.find(row => row.role === 'lead')
  const agentId = lead?.planning_agent_id
  const runId = team?.runId
  const turn = team?.turn
  const terminal = !team || ['done', 'failed', 'interrupted'].includes(team.phase)
  const name = lead?.displayName || lead?.name || ''
  const member = lead?.name || ''
  const teamName = team?.team || ''
  const [snapshot, setSnapshot] = useState<{ key: string; runId: string; blocks: Block[] } | null>(null)
  const key = JSON.stringify([owner, runId, agentId])
  useEffect(() => {
    if (!pageVisible || !owner || !agentId || !runId) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let failures = 0
    const load = async () => {
      try {
        const response = await api.getSession(owner, 'conversation-window', controller.signal, agentId)
        if (controller.signal.aborted) return
        if (response.ok) {
          failures = 0
          const rows = projectLeadConversation(blocksFromDetail(response.data), {runId, team: teamName, turn}, {name: member, displayName: name, agent: lead?.agent}, response.data.turn)
          setSnapshot({ key, runId, blocks: rows })
        } else failures++
      } catch { if (!controller.signal.aborted) failures++ }
      if (!controller.signal.aborted && (!terminal || failures > 0)) timer = setTimeout(load, Math.min(30000, 2000 * 2 ** Math.min(failures, 4)))
    }
    void load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [pageVisible, owner, agentId, runId, terminal, key, name, member, teamName, turn, lead?.agent])
  const saved = snapshot?.key === key ? snapshot.blocks : []
  const frame = lead && team?.previews?.[lead.name]
  if (team && lead && frame?.status === 'streaming' && frame.thinking) {
    const thoughts: Block[] = [...saved.filter(row => !(row.kind === 'thinking' && (row.streaming || row.text === frame.thinking))),
      { id: `lead-live-thinking:${team.runId}:${frame.requestId}`, kind: 'thinking', at: team.at, turn: team.turn,
        text: frame.thinking, streaming: !frame.text, author: { team: teamName, member, display_name: name, role: 'lead', stage: 'reply', agent: lead.agent } }]
    return { runId: team.runId, blocks: thoughts }
  }
  return snapshot?.key === key ? snapshot : null
}
