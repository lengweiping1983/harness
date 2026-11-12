import {saveAutomationOnce} from '@/lib/automationCreation'
import type {ResourceText} from '@/types'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import { handleTablistKeyDown } from '@/lib/tabNavigation'
import { Switch } from '@/components/ui/primitives'
import { t, useLocale } from '@/i18n'
import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, Clock3, Inbox, Plus, Search } from 'lucide-react'
import { Button, RefreshButton } from '@/components/ui/primitives'
import { useAppStore } from '@/store/app'
import { runtimeRequest } from '@/lib/http'
import { cn } from '@/lib/utils'
import { automationState, weekdayLabels, ZONES, dateLabel, inputTimestamp, localInput, runResult, scheduleLabel, type Automation, type AutomationPayload, type Schedule } from '@/lib/automations'

const field = 'w-full min-w-0 rounded-lg border border-line bg-canvas px-3 py-2 text-xs text-ink'
const statusKeys: Record<string, string> = {
  running: 'ui.components.overlays.AutomationsPage.1f0eb99b7ed0',
  completed: 'ui.components.overlays.AutomationsPage.f28461bb49c8',
  failed: 'ui.member.status.failed',
  interrupted: 'ui.member.status.interrupted',
}
const templateKeys = [
  {name: 'ui.components.overlays.AutomationsPage.582dfbcead41', prompt: 'ui.components.overlays.AutomationsPage.2824b8898d76'},
  {name: 'ui.components.overlays.AutomationsPage.720616f3c9b5', prompt: 'ui.components.overlays.AutomationsPage.c148ed02f9e2'},
  {name: 'ui.components.overlays.AutomationsPage.d3a1f32d040b', prompt: 'ui.components.overlays.AutomationsPage.f4bf4737548a'},
]

export function AutomationsPage() {
  useLocale()
  const templates = templateKeys.map(item => ({name: t(item.name), prompt: t(item.prompt)}))

  const sessions = useAppStore((s) => s.sessions)
  const sessionId = useAppStore((s) => s.activeSessionId)
  const [data, setData] = useState<AutomationPayload | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const [loadError, setLoadError] = useState<ResourceText>('')
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<'tasks' | 'runs'>('tasks')
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [editor, setEditor] = useState<Automation | 'new' | null>(null)
  const [template, setTemplate] = useState(templates[0])
  const [deleting, setDeleting] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const alive = useRef(true)
  const operationLock = useRef(false)
  const catalogEpoch = useRef(0)
  const reading = useRef<AbortController>()
  const refresh = async () => {
    reading.current?.abort()
    const request = reading.current = new AbortController(), ticket = ++catalogEpoch.current
    const result = await runtimeRequest<AutomationPayload>('/automations', 'GET', undefined, {signal: request.signal})
    if (alive.current && !request.signal.aborted && ticket === catalogEpoch.current) { setData(result); setLoadError('') }
  }
  useEffect(() => {
    alive.current = true
    let disposed = false
    let observation = 0
    let controller: AbortController | undefined
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      if (document.hidden || disposed) return
      if (operationLock.current) { timer = setTimeout(poll, 4000); return }
      const cycle = observation
      const request = controller = reading.current = new AbortController(), ticket = ++catalogEpoch.current
      try { const result = await runtimeRequest<AutomationPayload>('/automations', 'GET', undefined, { signal: request.signal }); if (alive.current && !request.signal.aborted && ticket === catalogEpoch.current) { setData(result); setLoadError('') } }
      catch (failure) { if (alive.current && !request.signal.aborted && ticket === catalogEpoch.current) setLoadError(failureText(failure)) }
      finally { if (!disposed && cycle === observation && alive.current && !document.hidden) timer = setTimeout(poll, 4000) }
    }
    const visibilityChanged = () => {
      ++observation
      clearTimeout(timer)
      reading.current?.abort()
      if (!document.hidden) void poll()
    }
    document.addEventListener('visibilitychange', visibilityChanged)
    void poll()
    return () => { disposed = true; alive.current = false; ++catalogEpoch.current; controller?.abort(); reading.current?.abort(); clearTimeout(timer); document.removeEventListener('visibilitychange', visibilityChanged) }
  }, [])
  const perform = async (operation: (() => Promise<unknown>) | null, message: ResourceText) => {
    if (operationLock.current) return false
    operationLock.current = true; ++catalogEpoch.current; reading.current?.abort()
    setBusy(true); setError('')
    try {
      if (operation) await operation()
      if (!alive.current) return false
      await refresh()
      if (!alive.current) return false
      useAppStore.getState().toast({ title: message, tone: 'success' }); return true
    }
    catch (failure) { if (alive.current) setError(failureText(failure)); return false }
    finally { operationLock.current = false; if (alive.current) setBusy(false) }
  }
  const openSession = async (id: string) => {
    await useAppStore.getState().refreshSessions()
    if (!alive.current) return
    useAppStore.getState().selectSession(id)
    useAppStore.getState().setSettingsOpen(false)
  }
  const normalizedQuery = query.trim().toLowerCase()
  const visibleTasks = data?.automations.filter(task =>
    task.name.toLowerCase().includes(normalizedQuery) && (filter === 'all' ||
      (filter === 'active' ? ['active', 'running'].includes(automationState(task, data.runs)) : automationState(task, data.runs) === filter))) ?? []
  const unread = data?.runs.filter((run) => run.unread && run.status !== 'running').length ?? 0
  if (editor) return <AutomationEditor key={typeof editor === 'string' ? 'new' : editor.id} task={editor === 'new' ? null : editor} initial={template} sessionId={sessionId} busy={busy} error={error} onCancel={() => { setEditor(null); setError('') }} onSave={async (body) => {
    if (await perform(() => saveAutomationOnce(body), {key: "ui.components.overlays.AutomationsPage.03a2964ef79b"})) setEditor(null)
  }} />
  return <div className="refresh-action-container space-y-4" aria-label={t("ui.components.overlays.AutomationsPage.7878929d8f9b")}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h3 className="ui-section-title">{t("ui.components.overlays.AutomationsPage.3c39e9bde120")}</h3><p className="mt-1 text-xs text-muted">{t("ui.components.overlays.AutomationsPage.3abfb827baf4")}</p></div>
      <Button size="sm" variant="primary" icon={<Plus className="h-3.5 w-3.5" />} disabled={!data || busy} onClick={() => { setTemplate({ name: '', prompt: '' }); setError(''); setEditor('new') }}>{t("ui.components.overlays.AutomationsPage.6bee2372805a")}</Button>
    </div>
    <p className="rounded-lg bg-raised px-3 py-2 text-2xs leading-relaxed text-muted">{t("ui.components.overlays.AutomationsPage.2d2f01774da4")}</p>
    {error || loadError ? <div role="alert" className="flex flex-wrap items-center justify-between gap-2 text-xs text-danger"><span>{noticeText(error || loadError)}</span><Button size="xs" disabled={busy} onClick={() => void perform(null, {key: "ui.components.overlays.AutomationsPage.371147927ebc"})}>{t("ui.components.overlays.AutomationsPage.b8784c8dd563")}</Button></div> : null}
    {data?.scheduler.last_error ? <p role="alert" className="text-xs text-danger">{t("ui.components.overlays.AutomationsPage.17893e78ce8d")}{data.scheduler.last_error}</p> : null}
    <div className="flex items-center justify-between border-b border-line">
      <div role="tablist" onKeyDown={handleTablistKeyDown} aria-label={t("ui.components.overlays.AutomationsPage.903b955fb996")} className="flex gap-4">{(['tasks', 'runs'] as const).map((item) => <button key={item} type="button" role="tab" tabIndex={tab === item ? 0 : -1} aria-selected={tab === item} aria-controls="automation-tabpanel" id={'automation-tab-' + item} className={cn('border-b-2 px-1 pb-2 text-xs', tab === item ? 'border-brand font-semibold text-brand' : 'border-transparent text-muted')} onClick={() => setTab(item)}>{item === 'tasks' ? t("ui.components.overlays.AutomationsPage.dd8a468ad11a", {p0: (data?.automations.length ?? 0)}) : t("ui.components.overlays.AutomationsPage.bbde7426c35c", {p0: (unread ? ' · ' + unread + t("ui.components.overlays.AutomationsPage.f891a85be514") : '')})}</button>)}</div>
      <RefreshButton size="xs" label={t("ui.components.overlays.AutomationsPage.4458d5c5befb")} disabled={busy} onClick={() => void perform(null, {key: "ui.components.overlays.AutomationsPage.371147927ebc"})} />
    </div>
    {!data ? <p role="status" className="text-xs text-muted">{error || loadError ? t("ui.components.overlays.AutomationsPage.c29ee94f637f") : t("ui.components.overlays.AutomationsPage.f6122dc87249")}</p> : <div role="tabpanel" id="automation-tabpanel" aria-labelledby={'automation-tab-' + tab} className="space-y-3">
      {tab === 'tasks' ? <>
        {data.automations.length ? <div className="flex flex-wrap gap-2"><label className="relative min-w-0 flex-1"><Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-faint" /><input aria-label={t("ui.components.overlays.AutomationsPage.374b7c866c67")} className={cn(field, 'pl-8')} placeholder={t("ui.components.overlays.AutomationsPage.5d02c355dcc5")} value={query} onChange={(e) => setQuery(e.target.value)} /></label><select aria-label={t("ui.components.overlays.AutomationsPage.3ed6012d1467")} className={cn(field, 'w-auto')} value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">{t("ui.components.overlays.AutomationsPage.0a379c1e7398")}</option><option value="active">{t("ui.components.overlays.AutomationsPage.dfb802238b38")}</option><option value="paused">{t("ui.components.overlays.AutomationsPage.eb0c326b60ae")}</option><option value="completed">{t("ui.components.overlays.AutomationsPage.f28461bb49c8")}</option></select></div> : <div className="py-3"><Clock3 className="mb-2 h-6 w-6 text-brand" /><h4 className="text-sm font-medium">{t("ui.components.overlays.AutomationsPage.af6b60fb8b75")}</h4><p className="mt-1 text-xs text-muted">{t("ui.components.overlays.AutomationsPage.562fde3366a2")}</p><div className="mt-3 divide-y divide-line rounded-lg border border-line">{templates.map((item) => <button key={item.name} className="flex w-full items-center justify-between gap-3 px-3 py-3 text-left hover:bg-raised" onClick={() => { setTemplate(item); setError(''); setEditor('new') }}><span><span className="block text-xs font-medium">{item.name}</span><span className="mt-1 block text-2xs text-muted">{item.prompt}</span></span><Plus className="h-4 w-4 shrink-0 text-brand" /></button>)}</div></div>}
        {visibleTasks.map((task) => <article key={task.id} className="space-y-2 border-b border-line pb-3">
          <div className="flex items-start justify-between gap-3"><h4 className="ui-card-title break-words">{task.name}</h4><span className={cn('shrink-0 rounded-md px-2 py-0.5 text-2xs', task.running ? 'bg-brand/10 text-brand' : task.enabled ? 'bg-ok/10 text-ok' : 'bg-raised text-muted')}>{task.running ? t("ui.components.overlays.AutomationsPage.1f0eb99b7ed0") : task.enabled ? t("ui.components.overlays.AutomationsPage.dfb802238b38") : automationState(task, data.runs) === 'completed' ? t("ui.components.overlays.AutomationsPage.f28461bb49c8") : t("ui.components.overlays.AutomationsPage.eb0c326b60ae")}</span></div>
          <p className="line-clamp-2 whitespace-pre-wrap break-words text-xs text-muted">{task.prompt}</p>
          <p className="text-2xs text-muted">{scheduleLabel(task.schedule)} · {task.timezone}</p>
          <p className="text-2xs text-faint">{task.enabled ? t("ui.components.overlays.AutomationsPage.ec441513c139", {p0: (dateLabel(task.due, task.timezone))}) : t("ui.components.overlays.AutomationsPage.ae99b0ce2ec8")} · {task.mode === 'fresh' ? t("ui.components.overlays.AutomationsPage.0129011e88b9") : t("ui.components.overlays.AutomationsPage.0d29b5ed7954")} · {sessions.find((s) => s.id === task.session_id)?.title ?? t("ui.components.overlays.AutomationsPage.f0a97b5ffc49")}</p>
          {task.last_error ? <p className="break-words text-2xs text-danger">{task.failures >= 3 ? t("ui.components.overlays.AutomationsPage.bf95f4fda9a7") : ''}{task.last_error}</p> : null}
          <div className="flex flex-wrap gap-1.5"><Button size="xs" disabled={busy} onClick={() => void perform(() => runtimeRequest(`/automations/${task.id}/${task.running ? 'stop' : 'run'}`, 'POST'), task.running ? {key: "ui.components.overlays.AutomationsPage.1b11e740dd66"} : {key: "ui.components.overlays.AutomationsPage.1b68b3fec69f"})}>{task.running ? t("ui.components.overlays.AutomationsPage.fbc8aea48b07") : t("ui.components.overlays.AutomationsPage.04eb539709cf")}</Button><Button size="xs" disabled={busy || Boolean(task.running)} onClick={() => { setError(''); setEditor(task) }}>{t("ui.components.overlays.AutomationsPage.051836569928")}</Button><Button size="xs" disabled={busy || Boolean(task.running)} onClick={() => void perform(() => runtimeRequest('/automations', 'POST', { ...task, enabled: !task.enabled }), task.enabled ? {key: "ui.components.overlays.AutomationsPage.26a6ba96cdee"} : {key: "ui.components.overlays.AutomationsPage.2ee4bc899e2a"})}>{task.enabled ? t("ui.components.overlays.AutomationsPage.8d12fc0d4eb2") : t("ui.components.overlays.AutomationsPage.f4f0ead1116b")}</Button><Button size="xs" variant="ghost" disabled={busy || Boolean(task.running)} onClick={() => setDeleting(task.id)}>{t("ui.components.overlays.AutomationsPage.2f9daa828907")}</Button></div>
          {deleting === task.id ? <div className="flex flex-wrap items-center gap-2 rounded-lg border border-danger/25 p-2 text-xs"><span>{t("ui.automations.confirmDelete", {name: task.name})}</span><Button size="xs" variant="danger" disabled={busy} onClick={() => void perform(() => runtimeRequest('/automations/' + task.id, 'DELETE'), {key: "ui.components.overlays.AutomationsPage.18693cb1dc8a"}).then((ok) => { if (ok) setDeleting(null) })}>{t("ui.components.overlays.AutomationsPage.a3ea3c17b401")}</Button><Button size="xs" disabled={busy} onClick={() => setDeleting(null)}>{t("ui.components.overlays.AutomationsPage.2cd0f3be8738")}</Button></div> : null}
        </article>)}
        {data.automations.length > 0 && !visibleTasks.length ? <div role="status" className="space-y-2 rounded-lg border border-line p-3"><p className="text-xs text-muted">{t("ui.components.overlays.AutomationsPage.0221f87d15aa")}</p><Button size="sm" onClick={() => {setQuery(''); setFilter('all')}}>{t('ui.automations.resetFilters')}</Button></div> : null}
      </> : <>
        {!data.runs.length ? <div className="py-5 text-center"><Inbox className="mx-auto mb-2 h-6 w-6 text-faint" /><p className="text-sm">{t("ui.components.overlays.AutomationsPage.932fea3f176b")}</p><p className="mt-1 text-xs text-muted">{t("ui.components.overlays.AutomationsPage.c5575226d43d")}</p></div> : null}
        {data.runs.map((run) => { const result = runResult(run); return <article key={run.id} className="border-b border-line pb-3">
          <button className="w-full space-y-1 text-left" aria-expanded={expanded === run.id} onClick={() => { setExpanded(expanded === run.id ? null : run.id); if (run.unread && run.status !== 'running') void perform(() => runtimeRequest('/automation-runs/' + run.id + '/read', 'POST'), {key: "ui.components.overlays.AutomationsPage.cf9addb2ff7f"}) }}><div className="flex items-center justify-between gap-2"><span className="break-words text-xs font-semibold">{run.unread && run.status !== 'running' ? <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-brand" /> : null}{run.name ?? data.automations.find((task) => task.id === run.automation_id)?.name ?? t("ui.components.overlays.AutomationsPage.f92d74c82783")}</span><span className={cn('shrink-0 text-2xs', run.status === 'failed' || run.status === 'interrupted' ? 'text-danger' : 'text-muted')}>{Object.hasOwn(statusKeys, run.status) ? t(statusKeys[run.status]) : run.status}</span></div><p className="text-2xs text-faint">{dateLabel(run.started)}{t("ui.components.overlays.AutomationsPage.a962b8024d6f")}{run.trigger === 'manual' ? t("ui.components.overlays.AutomationsPage.9f4a53360d91") : t("ui.components.overlays.AutomationsPage.59d818a60df3")}{run.finished ? t("ui.components.overlays.AutomationsPage.42f0bbe1cf69", {p0: (Math.max(0, Math.round(run.finished - run.started)))}) : ''}</p></button>
          {expanded === run.id ? <div className="mt-2 space-y-2"><p className={cn('max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-raised p-3 text-xs', result.error ? 'text-danger' : 'text-ink')}>{result.error || result.text || (run.status === 'running' ? t("ui.components.overlays.AutomationsPage.505072466e44") : t("ui.components.overlays.AutomationsPage.2b85d0890f38"))}</p>{run.session_id ? <Button size="xs" disabled={busy} onClick={() => void perform(() => openSession(run.session_id!), {key: "ui.components.overlays.AutomationsPage.695353ca99da"})}>{t("ui.components.overlays.AutomationsPage.885f8c01983b")}</Button> : null}</div> : null}
        </article> })}
      </>}
      <div className="space-y-1 text-2xs text-faint">
        <p>{data.scheduler.running ? data.scheduler.owner ? t("ui.components.overlays.AutomationsPage.810b101d9abd") : t("ui.components.overlays.AutomationsPage.4c7e1e699acd") : t("ui.components.overlays.AutomationsPage.07a5fc1885bb")}</p>
        <p>{t('ui.automations.concurrency', {count: data.scheduler.max_concurrent})}</p>
        {tab === 'runs' ? <p>{t('ui.automations.recentLimit', {count: 200})}</p> : null}
      </div>
    </div>}
  </div>
}

function AutomationEditor({ task, initial, sessionId, busy, error, onCancel, onSave }: { task: Automation | null; initial: { name: string; prompt: string }; sessionId: string | null; busy: boolean; error: ResourceText; onCancel: () => void; onSave: (body: unknown) => Promise<void> }) {
  useLocale()

  const sessions = useAppStore((s) => s.sessions)
  const [name, setName] = useState(task?.name ?? initial.name)
  const [prompt, setPrompt] = useState(task?.prompt ?? initial.prompt)
  const [target, setTarget] = useState(task?.session_id ?? sessionId ?? sessions[0]?.id ?? '')
  const [mode, setMode] = useState(task?.mode ?? 'fresh')
  const [kind, setKind] = useState<Schedule['kind']>(task?.schedule.kind ?? 'daily')
  const [zone, setZone] = useState(task?.timezone ?? 'Asia/Shanghai')
  const [clock, setClock] = useState(task && 'time' in task.schedule ? task.schedule.time : '09:00')
  const [days, setDays] = useState(task?.schedule.kind === 'weekly' ? task.schedule.days : [0, 1, 2, 3, 4])
  const [monthdays, setMonthdays] = useState(task && (task.schedule.kind === 'monthly' || task.schedule.kind === 'yearly') ? task.schedule.monthdays : [1])
  const [months, setMonths] = useState(task?.schedule.kind === 'yearly' ? task.schedule.months : [1])
  const [minutes, setMinutes] = useState(task?.schedule.kind === 'interval' ? task.schedule.seconds / 60 : 60)
  const [at, setAt] = useState(localInput(task?.due ?? Date.now() / 1000 + 3600, zone))
  const [enabled, setEnabled] = useState(task ? Boolean(task.enabled) : true)
  const [preview, setPreview] = useState<number[]>([])
  const [previewError, setPreviewError] = useState<ResourceText>('')
  const titleRef = useRef<HTMLInputElement>(null)
  const composing = useRef(false)
  useEffect(() => { titleRef.current?.focus() }, [])
  const schedule: Schedule = kind === 'once' ? { kind, at: inputTimestamp(at, zone) } : kind === 'interval' ? { kind, seconds: minutes * 60 } : kind === 'daily' ? { kind, time: clock } : kind === 'monthly' ? { kind, time: clock, monthdays } : kind === 'yearly' ? { kind, time: clock, months, monthdays } : { kind, time: clock, days }
  const encoded = JSON.stringify(schedule)
  useEffect(() => {
    const controller = new AbortController()
    setPreview([]); setPreviewError('')
    const timer = setTimeout(() => { void runtimeRequest<{ occurrences: number[] }>('/automations/preview', 'POST', { schedule: JSON.parse(encoded), timezone: zone }, { signal: controller.signal }).then((result) => { if (!controller.signal.aborted) setPreview(result.occurrences) }).catch((failure: Error) => { if (!controller.signal.aborted) setPreviewError(failureText(failure)) }) }, 250)
    return () => { clearTimeout(timer); controller.abort() }
  }, [encoded, zone])
  return <form className="space-y-4" aria-label={task ? t("ui.components.overlays.AutomationsPage.6d931444b2c7") : t("ui.components.overlays.AutomationsPage.f36aa4aa87ff")} onCompositionStart={() => { composing.current = true }} onCompositionEnd={() => { composing.current = false }} onKeyDown={(e) => { if (e.key === 'Enter' && (composing.current || e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229)) e.preventDefault() }} onSubmit={(e) => { e.preventDefault(); if (composing.current) return; void onSave({ ...(task ? { id: task.id } : {}), name, prompt, session_id: target, mode, schedule, timezone: zone, enabled }) }}>
    <div className="flex items-center gap-2"><Button size="xs" variant="ghost" aria-label={t("ui.components.overlays.AutomationsPage.d3e1da94411f")} icon={<ArrowLeft className="h-3.5 w-3.5" />} disabled={busy} onClick={onCancel} /><h3 className="ui-section-title">{task ? t("ui.components.overlays.AutomationsPage.6d931444b2c7") : t("ui.components.overlays.AutomationsPage.f36aa4aa87ff")}</h3></div>
    {error ? <p role="alert" className="text-xs text-danger">{noticeText(error)}</p> : null}
    <fieldset disabled={busy} className="min-w-0 space-y-3">
      <label className="block space-y-1.5 text-xs"><span>{t("ui.components.overlays.AutomationsPage.2479560deb33")}</span><input aria-label={t("ui.components.overlays.AutomationsPage.2479560deb33")} ref={titleRef} className={field} required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} placeholder={t("ui.components.overlays.AutomationsPage.11b494bd5f9d")} /></label>
      <label className="block space-y-1.5 text-xs"><span>{t("ui.components.overlays.AutomationsPage.2b7e32bffda6")}</span><textarea aria-label={t("ui.components.overlays.AutomationsPage.2b7e32bffda6")} className={cn(field, 'min-h-24 resize-y')} required maxLength={32000} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder={t("ui.components.overlays.AutomationsPage.17e100d058f7")} /></label>
      <label className="block space-y-1.5 text-xs"><span>{t("ui.components.overlays.AutomationsPage.a8f9c53b7ada")}</span><select aria-label={t("ui.components.overlays.AutomationsPage.a8f9c53b7ada")} className={field} required value={target} onChange={(e) => setTarget(e.target.value)}><option value="">{t("ui.components.overlays.AutomationsPage.4fc5349db1ff")}</option>{sessions.map((session) => <option key={session.id} value={session.id}>{session.title || t("ui.components.overlays.AutomationsPage.a91ae6035092")} — {session.cwd}</option>)}</select></label>
      {!sessions.length ? <p className="text-xs text-warn">{t("ui.components.overlays.AutomationsPage.57e10c707337")}</p> : null}
      <label className="block space-y-1.5 text-xs"><span>{t("ui.components.overlays.AutomationsPage.9474ec219e80")}</span><select aria-label={t("ui.components.overlays.AutomationsPage.9474ec219e80")} className={field} value={mode} onChange={(e) => setMode(e.target.value as 'fresh' | 'session')}><option value="fresh">{t("ui.components.overlays.AutomationsPage.073ca0505539")}</option><option value="session">{t("ui.components.overlays.AutomationsPage.3a19ae461d24")}</option></select></label>
      <p className="text-2xs text-muted">{t("ui.components.overlays.AutomationsPage.16f6473fec04")}</p>
      <div className="grid grid-cols-2 gap-3"><label className="space-y-1.5 text-xs"><span>{t("ui.components.overlays.AutomationsPage.15a1d21d96f9")}</span><select aria-label={t("ui.components.overlays.AutomationsPage.15a1d21d96f9")} className={field} value={kind} onChange={(e) => setKind(e.target.value as Schedule['kind'])}><option value="daily">{t("ui.components.overlays.AutomationsPage.eea1694c235b")}</option><option value="weekly">{t("ui.components.overlays.AutomationsPage.92845d3b531d")}</option><option value="monthly">{t("ui.components.overlays.AutomationsPage.kindMonthly")}</option><option value="yearly">{t("ui.components.overlays.AutomationsPage.kindYearly")}</option><option value="interval">{t("ui.components.overlays.AutomationsPage.6c9084f96f7f")}</option><option value="once">{t("ui.components.overlays.AutomationsPage.7c04effc4339")}</option></select></label><label className="space-y-1.5 text-xs"><span>{t("ui.components.overlays.AutomationsPage.b5d72c5c00f2")}</span><select aria-label={t("ui.components.overlays.AutomationsPage.b5d72c5c00f2")} className={field} value={zone} onChange={(e) => setZone(e.target.value)}>{Array.from(new Set([...ZONES, zone])).map((item) => <option key={item}>{item}</option>)}</select></label></div>
      {kind === 'once' ? <label className="block space-y-1.5 text-xs"><span>{t("ui.components.overlays.AutomationsPage.869783309197")}</span><input className={field} type="datetime-local" required value={at} onChange={(e) => setAt(e.target.value)} /></label> : kind === 'interval' ? <label className="block space-y-1.5 text-xs"><span>{t("ui.components.overlays.AutomationsPage.b8ce113470ed")}</span><input className={field} type="number" min={1} max={525600} required value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} /></label> : <label className="block space-y-1.5 text-xs"><span>{t("ui.components.overlays.AutomationsPage.7e1214b78bed")}</span><input className={field} type="time" required value={clock} onChange={(e) => setClock(e.target.value)} /></label>}
      {kind === 'weekly' ? <fieldset><legend className="mb-2 text-xs">{t("ui.components.overlays.AutomationsPage.0dfd10a53b82")}</legend><div className="flex flex-wrap gap-1.5">{weekdayLabels().map((day, index) => <button type="button" key={day} aria-label={t('ui.automations.weekdayAccessible', {day})} aria-pressed={days.includes(index)} className={cn('h-9 w-9 rounded-lg border text-xs', days.includes(index) ? 'border-brand bg-brand/10 text-brand' : 'border-line text-muted')} onClick={() => setDays(days.includes(index) ? days.filter((item) => item !== index) : [...days, index].sort())}>{day}</button>)}</div></fieldset> : null}
      {kind === 'yearly' ? <fieldset><legend className="mb-2 text-xs">{t("ui.components.overlays.AutomationsPage.monthsLegend")}</legend><div className="flex flex-wrap gap-1.5">{Array.from({length: 12}, (_, index) => index + 1).map((month) => <button type="button" key={month} aria-label={t('ui.automations.monthAccessible', {month})} aria-pressed={months.includes(month)} className={cn('h-9 w-9 rounded-lg border text-xs', months.includes(month) ? 'border-brand bg-brand/10 text-brand' : 'border-line text-muted')} onClick={() => setMonths(months.includes(month) ? months.filter((item) => item !== month) : [...months, month].sort((a, b) => a - b))}>{month}</button>)}</div></fieldset> : null}
      {kind === 'monthly' || kind === 'yearly' ? <fieldset><legend className="mb-2 text-xs">{t("ui.components.overlays.AutomationsPage.monthDaysLegend")}</legend><div className="flex flex-wrap gap-1">{Array.from({length: 31}, (_, index) => index + 1).map((day) => <button type="button" key={day} aria-label={t('ui.automations.monthdayAccessible', {day})} aria-pressed={monthdays.includes(day)} className={cn('h-8 w-8 rounded-md border text-2xs', monthdays.includes(day) ? 'border-brand bg-brand/10 text-brand' : 'border-line text-muted')} onClick={() => setMonthdays(monthdays.includes(day) ? monthdays.filter((item) => item !== day) : [...monthdays, day].sort((a, b) => a - b))}>{day}</button>)}</div></fieldset> : null}
      <div className="rounded-lg border border-line bg-raised p-3"><p className="mb-1 text-xs font-medium">{t("ui.components.overlays.AutomationsPage.3f09cb682642")}</p>{previewError ? <p role="alert" className="text-xs text-danger">{noticeText(previewError)}</p> : preview.length ? <div className="flex flex-wrap gap-x-3 gap-y-1 text-2xs text-muted">{preview.map((timestamp) => <span key={timestamp}>{dateLabel(timestamp, zone)}</span>)}</div> : <p role="status" className="text-2xs text-muted">{t("ui.components.overlays.AutomationsPage.e1fa1bf0cd78")}</p>}{kind === 'once' && preview[0] <= Date.now() / 1000 ? <p className="mt-1 text-2xs text-warn">{t("ui.components.overlays.AutomationsPage.e842cc9a421b")}</p> : null}</div>
      <label className="flex items-center gap-2 text-xs"><Switch checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />{t("ui.components.overlays.AutomationsPage.5f86080440c7")}</label>
    </fieldset>
    <div className="sticky bottom-0 flex gap-2 border-t border-line bg-overlay py-3"><Button type="submit" variant="primary" size="sm" loading={busy} disabled={!name.trim() || !prompt.trim() || !target || !preview.length || Boolean(previewError)}>{t("ui.components.overlays.AutomationsPage.24a015d6239c")}</Button><Button size="sm" disabled={busy} onClick={onCancel}>{t("ui.components.overlays.AutomationsPage.2cd0f3be8738")}</Button></div>
  </form>
}
