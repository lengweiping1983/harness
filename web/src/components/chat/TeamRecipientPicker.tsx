import { useId, useRef, useState } from 'react'
import { t, useLocale } from '@/i18n'
import { teamRecipientValue, validTeamAddress, makeTeamAddress, type AgentMountState, type TeamAddress } from '@/lib/agentMount'

/** 结构化收件人独立于输入文本，代码、邮件地址与引用不会误触发派工。 */
export function TeamRecipientPicker({ mount, address, onChange }: {
  mount: AgentMountState | null; address?: TeamAddress; onChange: (address?: TeamAddress) => void
}) {
  useLocale()
  const [expanded, setExpanded] = useState(false)
  const choicesId = useId()
  const toggleRef = useRef<HTMLButtonElement>(null)
  const selectRef = useRef<HTMLSelectElement>(null)
  const valid = validTeamAddress(address, mount)
  const members = mount?.members ?? []
  const value = teamRecipientValue(address, mount)
  const selected = valid ? address?.members ?? [] : []
  const selectMembers = (names: string[]) => {
    if (!mount?.agent || mount.agent_type !== 'team') return
    onChange(makeTeamAddress(mount, names))
  }
  return <div className="space-y-1 border-b border-line px-3 py-2">
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
      <span>{t('ui.team.recipients.label')}</span>
      <select ref={selectRef} aria-label={t('ui.team.recipients.label')} value={value} className="min-w-0 max-w-full rounded-lg border border-line bg-canvas px-2 py-1.5 text-xs text-ink" onChange={event => {
        const selected = event.target.value
        if (!selected) { onChange(); return }
        if (!mount?.agent || mount.agent_type !== 'team') return
        onChange(makeTeamAddress(mount, selected === '@all' ? members.map(member => member.name) : [selected]))
      }}>
        <option value="">{t('ui.team.recipients.dispatch')}</option>
        {value === '@custom' && <option value="@custom" disabled>{t('ui.team.recipients.multiple')}</option>}
        {value === '@empty' && <option value="@empty" disabled>{t('ui.team.recipients.empty')}</option>}
        {value === '@stale' && <option value="@stale" disabled>{t('ui.team.recipients.stale')}</option>}
        {members.map(member => <option key={member.name} value={member.name}>{member.display_name} · {t(member.role === 'lead' ? 'ui.agent.author.lead' : 'ui.agent.author.worker')}</option>)}
        {members.length > 1 && <option value="@all">{t('ui.team.recipients.all')}</option>}
      </select>
      {members.length > 1 && <button ref={toggleRef} type="button" aria-controls={choicesId} aria-expanded={expanded} className="rounded-md px-2 py-1 text-brand hover:bg-raised" onClick={() => setExpanded(open => !open)}>{t('ui.team.recipients.chooseMultiple')}</button>}
      {address && <button type="button" className="rounded-md px-2 py-1 text-muted hover:bg-raised hover:text-ink" onClick={() => { onChange(); setExpanded(false); selectRef.current?.focus() }}>{t('ui.team.recipients.dispatch')}</button>}
    </div>
    {valid && selected.length > 1 && <p className="text-xs leading-relaxed text-brand [overflow-wrap:anywhere]">{t('ui.team.recipients.selected')} · {selected.map(name => members.find(member => member.name === name)?.display_name || name).join(t('ui.common.list_separator'))}</p>}
    {expanded && <fieldset id={choicesId} onKeyDown={event => { if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return; if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setExpanded(false); toggleRef.current?.focus() } }} className="min-w-0 space-y-2 rounded-lg border border-line bg-canvas p-2"><legend className="px-1 text-xs text-muted">{t('ui.team.recipients.multiple')}</legend><div className="flex flex-wrap gap-x-4 gap-y-2">{members.map(member => <label key={member.name} className="inline-flex min-w-0 max-w-full items-center gap-2 text-xs text-ink"><input className="shrink-0" type="checkbox" aria-label={t('ui.team.recipients.selectMember') + ' · ' + member.display_name} checked={selected.includes(member.name)} onChange={event => selectMembers(event.target.checked ? members.filter(row => selected.includes(row.name) || row.name === member.name).map(row => row.name) : selected.filter(name => name !== member.name))} /><span className="min-w-0 [overflow-wrap:anywhere]">{member.display_name}</span></label>)}</div><p className="text-2xs text-faint">{t('ui.team.recipients.multipleHint')}</p></fieldset>}
    {value === '@empty' ? <p role="alert" className="text-xs text-warn">{t('ui.team.recipients.emptyHint')}</p> : !valid ? <p role="alert" className="text-xs text-warn">{t('ui.team.recipients.changed')}</p> : address ? <p className="text-2xs text-faint">{t('ui.team.recipients.directHint')}</p> : null}
  </div>
}