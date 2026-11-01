import { AgentAvatar } from '@/components/ui/AgentAvatar'
import { useState } from 'react'
import { Search, Plus, Check } from 'lucide-react'
import { t, useLocale } from '@/i18n'

type Agent = { name: string; display_name?: string; profession?: string; description: string; agent_type: 'agent' | 'team'; enabled?: boolean; avatar?: string | null }

/** 可选 Agent 与已选成员分栏，用户只需阅读名称和职责。 */
export function TeamMemberPicker({ agents, selected, count, valid, onAdd, onAddExternal, sessionId }: {
  agents: Agent[]; selected: string[]; count: number; valid: boolean; onAdd: (name: string) => void; onAddExternal: (runner: 'codex' | 'claude' | 'opencode') => void; sessionId?: string | null
}) {
  useLocale()
  const [query, setQuery] = useState('')
  const needle = query.trim().toLocaleLowerCase()
  const matches = agents.filter(agent => agent.agent_type === 'agent' && agent.enabled !== false
    && `${agent.name} ${agent.display_name ?? ''} ${agent.profession ?? ''} ${agent.description}`.toLocaleLowerCase().includes(needle))
  const label = (key: string) => t('ui.agent.author.' + key)
  const canAdd = (name: string) => valid && count < 8 && !selected.includes(name)
  return <section aria-label={label('availableAgents')} className="flex h-team-picker min-h-80 flex-col rounded-2xl border border-line bg-canvas">
    <div className="border-b border-line p-4">
      <label className="ui-input-group flex items-center gap-3 rounded-lg border border-line bg-surface px-3 py-3"><Search aria-hidden className="h-4 w-4 shrink-0 text-faint" />
        <input aria-label={label('memberSearch')} placeholder={label('memberSearch')} value={query} onChange={event => setQuery(event.target.value)}
          className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none" onKeyDown={event => {
            if (event.key !== 'Enter' || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
            event.preventDefault()
            const agent = matches.find(agent => canAdd(agent.name))
            if (agent) onAdd(agent.name)
          }} />
      </label>
    </div>
    <details className="border-b border-line p-4"><summary className="cursor-pointer text-sm font-medium text-ink">{label('externalMembers')}</summary><div className="mt-3 space-y-3">
      <p className="text-xs leading-relaxed text-muted">{label('externalMembersHelp')}</p>
      <div className="flex flex-wrap gap-2">{(['codex', 'claude', 'opencode'] as const).map(runner => {
        const name = runner === 'codex' ? 'Codex' : runner === 'claude' ? 'Claude Code' : 'OpenCode'
        return <button key={runner} type="button" disabled={!valid || count >= 8} aria-label={label('add') + ' · ' + name} onClick={() => onAddExternal(runner)} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-xs font-medium text-ink hover:border-brand/40 disabled:opacity-40"><Plus aria-hidden className="h-3.5 w-3.5" />{name}</button>
      })}</div>
    </div></details>
    <div className="flex items-center justify-between gap-3 px-4 py-3 text-sm text-muted"><h3 className="font-medium">{label('availableAgents')}</h3><span className="text-xs text-faint">{matches.length}</span></div>
    {!valid ? <p role="alert" className="px-4 pb-3 text-xs text-warn">{label('memberInvalid')}</p> : count >= 8 ? <p role="status" className="px-4 pb-3 text-xs text-muted">{label('memberLimit')}</p> : null}
    <div className="min-h-0 flex-1 space-y-2 overflow-y-auto overscroll-contain px-4 pb-4">
      {matches.map(agent => {
        const added = selected.includes(agent.name)
        const name = agent.display_name || agent.name
        return <button key={agent.name} type="button" disabled={!canAdd(agent.name)} onClick={() => onAdd(agent.name)}
          aria-label={label('add') + ' · ' + name + ' · ' + agent.name}
          className={'flex w-full items-center gap-3 rounded-xl border p-3 text-left transition-colors focus-visible:outline-brand disabled:cursor-default ' + (added ? 'border-brand/20 bg-brand/5' : 'border-line bg-surface hover:border-brand/30 hover:bg-raised')}>
          <AgentAvatar src={agent.avatar ? '/api/v1/plugins' + agent.avatar + (sessionId ? '?session_id=' + encodeURIComponent(sessionId) : '') : null} />
          <div className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-ink">{name}</span><span className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted">{agent.profession || agent.description}</span></div>
          <span className={'flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ' + (added ? 'bg-brand/10 text-brand' : 'bg-raised text-brand')}>{added ? <Check aria-hidden className="h-4 w-4" /> : <Plus aria-hidden className="h-5 w-5" />}</span>
          {added && <span className="sr-only">{label('memberAdded')}</span>}
        </button>
      })}
      {matches.length === 0 && <p role="status" className="py-8 text-center text-sm text-muted">{label('memberEmpty')}</p>}
    </div>
  </section>
}
