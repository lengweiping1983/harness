import type { ResourceText } from '@/types'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { Switch } from '@/components/ui/primitives'
import { useEffect, useId, useRef, useState } from 'react'
import { t, useLocale } from '@/i18n'
import { pluginRequest } from '@/lib/http'
import { Button } from '@/components/ui/primitives'

type Skill = { name: string; description: string; enabled: boolean; plugin_enabled: boolean; interface?: { display_name?: string; short_description?: string } }

export function AgentSkillPicker({ sessionId, value, onChange, external = false }: {
  sessionId: string | null; value: string[] | null; onChange: (value: string[] | null) => void; external?: boolean
}) {
  const locale = useLocale()
  const id = useId()
  const explicitSelection = useRef<string[]>(value === null ? [] : [...value])
  const [expanded, setExpanded] = useState(false)
  const [catalog, setCatalog] = useState<Skill[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [revision, setRevision] = useState(0)
  const [query, setQuery] = useState('')
  const [enabled, setEnabled] = useState(true)
  useEffect(() => {
    if (!expanded || external) return
    const controller = new AbortController()
    setLoading(true); setError(''); setCatalog([])
    void pluginRequest<{ skills: Skill[]; enabled: boolean }>('/skills?view=picker' + (sessionId ? '&session_id=' + encodeURIComponent(sessionId) : ''), 'GET', undefined, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) { setCatalog(data.skills); setEnabled(data.enabled) } })
      .catch(failure => { if (!controller.signal.aborted) setError(failureText(failure)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [expanded, external, sessionId, revision, locale])
  const selected = value ?? catalog.filter(skill => skill.enabled).map(skill => skill.name)
  const visible = catalog.filter(skill => [skill.name, skill.description, skill.interface?.display_name, skill.interface?.short_description].join(' ').toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  const missing = (value ?? []).filter(name => !catalog.some(skill => skill.name === name))
  const label = (key: string) => t('ui.agent.skills.' + key)
  if (external) return <div className="rounded-xl border border-line bg-canvas p-4"><p className="text-sm font-medium text-ink">{label('title')}</p><p className="mt-2 text-xs leading-relaxed text-muted">{label('externalHint')}</p></div>
  return <details className="rounded-xl border border-line bg-canvas p-4" open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary className="cursor-pointer text-sm font-medium text-ink">{label('title')} <span className="ml-2 text-xs font-normal text-muted">{value === null ? label('inherited') : t('ui.agent.skills.count', { count: value.length })}</span></summary>
    {expanded && <div className="mt-4 space-y-3">
      <p className="text-xs leading-relaxed text-muted">{label('hint')}</p>
      <label className="flex items-start gap-2 text-sm text-ink"><Switch checked={value === null} onChange={event => { if (event.target.checked) { explicitSelection.current = [...(value ?? [])]; onChange(null) } else onChange([...explicitSelection.current]) }} />{label('inherit')}</label>
      {loading && <p role="status" className="text-xs text-muted">{label('loading')}</p>}
      {error && <div role="alert" className="space-y-2 text-xs text-danger"><p className="break-words">{noticeText(error)}</p><Button size="sm" variant="secondary" onClick={() => setRevision(current => current + 1)}>{label('retry')}</Button></div>}
      {!loading && !error && !enabled && <p role="status" className="text-xs text-warn">{label('disabled')}</p>}
      {!loading && !error && enabled && <>
        <label className="block space-y-2 text-xs text-muted" htmlFor={id}>{label('search')}<input id={id} type="search" className="ui-field w-full" value={query} onChange={event => setQuery(event.target.value)} /></label>
        <div className="flex flex-wrap gap-2"><Button size="sm" variant="secondary" onClick={() => onChange([...new Set([...selected, ...visible.filter(skill => skill.plugin_enabled).map(skill => skill.name)])])}>{label('selectVisible')}</Button><Button size="sm" variant="ghost" onClick={() => onChange([])}>{label('clear')}</Button></div>
        <div className="max-h-64 divide-y divide-line overflow-y-auto overscroll-contain">
          {visible.map(skill => <label key={skill.name} className="flex items-start gap-3 py-3 text-sm text-ink">
            <Switch className="order-last mt-1" aria-label={skill.name} checked={selected.includes(skill.name)} disabled={!skill.plugin_enabled && !selected.includes(skill.name)} onChange={event => onChange(event.target.checked ? [...selected, skill.name] : selected.filter(name => name !== skill.name))} />
            <span className="min-w-0 flex-1 break-words"><span className="block font-medium">{skill.interface?.display_name || skill.name}</span><span className="mt-1 block text-xs text-muted">{skill.interface?.short_description || skill.description}</span>{!skill.plugin_enabled && <span className="mt-1 block text-xs text-warn">{label('pluginDisabled')}</span>}</span>
          </label>)}
          {!visible.length && <p role="status" className="py-3 text-xs text-muted">{label(catalog.length ? 'noMatches' : 'empty')}</p>}
        </div>
        {missing.map(name => <label key={name} className="flex items-center gap-2 break-all text-xs text-warn"><Switch aria-label={name} checked onChange={() => onChange((value ?? []).filter(item => item !== name))} />{name} · {label('missing')}</label>)}
        <p role="status" className="text-xs text-muted">{t('ui.agent.skills.count', { count: selected.length })}</p>
        {value?.length === 0 && <p className="text-xs text-muted">{label('noneHint')}</p>}
      </>}
    </div>}
  </details>
}
