import { memo, useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { Block } from '@/types'
import { t, useLocale } from '@/i18n'
import { useChatStore } from '@/store/chat'
import { useAppStore } from '@/store/app'
import { AgentAvatar } from '@/components/ui/AgentAvatar'
import { cn } from '@/lib/utils'
import { dockMembers, dockStatus, type DockMember } from '@/lib/memberDock'
import { teamAuthorAvatar } from '@/lib/teamAuthor'

export function MemberDock({ sessionId, blocks }: { sessionId: string; blocks: Block[] }) {
  useLocale()
  const allMembers = useMemo(() => dockMembers(blocks, true), [blocks])
  const [expanded, setExpanded] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const currentMembers = allMembers.filter(member => !member.historical)
  const members = showHistory ? [...currentMembers, ...allMembers.filter(member => member.historical)] : currentMembers
  const status = dockStatus(currentMembers)
  const hasHistory = allMembers.some(member => member.historical)
  const selected = useAppStore(state => state.inspectorOpen && state.inspectorTab.startsWith('member:') ? state.inspectorMemberTarget : null)
  if (!members.length && !hasHistory) return null
  return <section aria-label={t('ui.member.dock')} className="mb-3 space-y-1">
    <div className="flex items-center gap-2 px-2 py-1 text-xs text-muted">
      {members.length > 0 && <button type="button" aria-label={t('ui.member.toggle')} aria-expanded={expanded} onClick={() => setExpanded(value => !value)} className="flex items-center gap-2 rounded py-1 focus-visible:outline-brand">
      <div className="flex -space-x-1">{members.slice(0, 4).map(member => <AgentAvatar key={member.key} src={memberAvatar(member, sessionId)} name={member.name} className="h-5 w-5 rounded-full text-2xs" />)}</div>
      <span>{t(members.length === 1 ? 'ui.member.oneMember' : 'ui.member.dockCount', { count: members.length })}</span>{status && !showHistory && <span className={cn('text-2xs', status === 'failed' ? 'text-danger' : ['blocked', 'interrupted'].includes(status) ? 'text-warn' : status === 'running' ? 'text-brand' : 'text-muted')}>{t('ui.member.status.' + status)}</span>}<ChevronRight aria-hidden className={cn('h-3 w-3 transition-transform', expanded && 'rotate-90')} /></button>}
      {hasHistory && <button type="button" aria-pressed={showHistory} onClick={() => { setShowHistory(value => !value); setExpanded(true) }} className="ml-auto rounded px-1.5 py-1 text-2xs text-muted hover:bg-raised focus-visible:outline-brand">{t(showHistory ? 'ui.member.current' : 'ui.member.history')}</button>}
    </div>
    {expanded && <div className="max-h-40 space-y-1 overflow-y-auto p-1">{members.map(member => {
      const active = selected?.owner === sessionId && selected.id === member.id
      return <button key={member.key} type="button" disabled={!member.id} aria-expanded={active} aria-controls={active ? 'inspector-tab-content' : undefined}
        onClick={event => { if (member.id) useAppStore.getState().revealMember({ owner: sessionId, id: member.id, name: member.name, agent: member.agent, role: member.role, leadName: member.leadName, leadAgent: member.leadAgent, contextSessionId: member.contextSessionId, preview: member.preview }, event.currentTarget) }}
        className={cn('flex w-full items-center gap-2 rounded-lg border px-2.5 py-2 text-left text-xs transition-colors hover:bg-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:cursor-default', active ? 'border-brand/30 bg-brand/5' : 'border-transparent bg-raised/40')}>
        <AgentAvatar src={memberAvatar(member, sessionId)} name={member.name} className="h-6 w-6 rounded-full text-2xs" />
        <span className="min-w-0 flex-1 text-ink"><span className="block truncate" title={member.name}>{member.name}{member.role === 'lead' && member.name !== t('ui.team.author.lead') && <span className="ml-1.5 text-2xs text-muted">{t('ui.team.author.lead')}</span>}</span>{showHistory && member.historical && member.group && <span className="block truncate text-2xs text-muted" title={member.group}>{member.group}</span>}</span>
        <span className={cn('shrink-0 text-2xs', member.status === 'running' ? 'text-brand' : member.status === 'done' ? 'text-ok' : member.status === 'failed' ? 'text-danger' : ['blocked', 'interrupted'].includes(member.status) ? 'text-warn' : 'text-muted')}>{t('ui.member.status.' + member.status)}</span>
        {member.id && <ChevronRight aria-hidden className="h-3 w-3 shrink-0 text-faint" />}
      </button>
    })}</div>}
  </section>
}

export const SessionMemberDock = memo(function SessionMemberDock({ sessionId }: { sessionId: string }) {
  const blocks = useChatStore(state => state.sessions[sessionId]?.blocks)
  return blocks ? <MemberDock sessionId={sessionId} blocks={blocks} /> : null
})

function memberAvatar(member: DockMember, sessionId: string): string | undefined {
  return member.agent ? teamAuthorAvatar({ team: '', member: member.key, display_name: member.name, role: member.role === 'lead' ? 'lead' : 'member', stage: 'member', agent: member.agent }, sessionId) : undefined
}
