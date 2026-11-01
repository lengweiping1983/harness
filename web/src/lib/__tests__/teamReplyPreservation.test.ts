import { t, setLocale } from '@/i18n'
import { describe, expect, it } from 'vitest'
import type { AssistantBlock, TeamBlock } from '@/types'
import { projectLeadConversation } from '../leadConversation'
import { memberStatus, dockMembers, mainConversationBlocks } from '../memberDock'

const texts = [
  '{"action":"delegate","reason":"example","assignments":[{"member":"worker","subtask":"example task"}]}',
  '{"action":"reply","reply":"example result","reason":"example"}',
]
const team: TeamBlock = { id: 'team', kind: 'team', at: 1, runId: 'run', team: 'fixture', turn: 3,
  phase: 'done', members: [{ name: 'lead', role: 'lead', status: 'done' }] }

describe('team replies preserve arbitrary JSON deliverables', () => {
  it.each(texts)('keeps the complete persisted lead message: %s', text => {
    const block: AssistantBlock = { id: 'reply', kind: 'assistant', at: 2, turn: 1, text }
    const projected = projectLeadConversation([block], team, team.members[0]!, 1)
    expect(projected).toHaveLength(1)
    expect(projected[0]).toMatchObject({ kind: 'assistant', text, turn: 3 })
  })
  it.each(texts)('keeps the complete public reply preview: %s', text => {
    const block: TeamBlock = { ...team, publicReply: { member: 'lead', displayName: 'Lead',
      agentId: 'member', requestId: 'request', text, stage: 'summary', truncated: false } }
    const replies = mainConversationBlocks([block]).filter(row => row.kind === 'assistant')
    expect(replies).toHaveLength(1)
    expect(replies[0]).toMatchObject({ text, author: { member: 'lead', role: 'lead' } })
  })
})

describe('member dock accepts current status values', () => {
  it.each(['pending','running','done','failed','blocked','interrupted'])('preserves current team status %s', status => {
    expect(memberStatus(status)).toBe(status)
  })
  it('maps current standalone completion to its display status', () => {
    expect(memberStatus('completed')).toBe('done')
  })
  it.each(['error','ERROR','RUNNING','Completed','constructor',''])('rejects obsolete or unknown status %s', status => {
    expect(memberStatus(status)).toBe('unknown')
    expect(dockMembers([{id:'agent',kind:'subagent',at:1,agentId:'agent',description:'Original 原文',status}])[0]).toMatchObject({status:'unknown',name:'Original 原文'})
  })
})

it('renders team assignments using current locale and exact status values', () => {
  for (const locale of ['en','zh-CN','en'] as const) {
    setLocale(locale)
    for (const status of ['pending','running','done','failed','blocked','interrupted','error','RUNNING']) {
      const block: TeamBlock = {...team, assignments:{worker:'Original task 原文'}, members:[team.members[0]!,{name:'worker',displayName:'Original worker 原文',role:'member',status}]}
      const result=mainConversationBlocks([block]).find(row=>row.kind==='assistant')
      expect(result).toMatchObject({text:t('ui.team.assignment',{name:'Original worker 原文',status:t('ui.member.status.'+memberStatus(status)),task:'Original task 原文'})})
    }
  }
})
