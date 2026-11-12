import type { ResourceText } from '@/types'
import { failureText, ResourceError } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { PluginHooksSection } from './PluginHooks'
import { t, useLocale , getLocale } from '@/i18n'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { handleTablistKeyDown } from '@/lib/tabNavigation'
import { GitBranch, ShieldCheck, Terminal } from 'lucide-react'
import { Button, RefreshButton } from '@/components/ui/primitives'
import { useAppStore } from '@/store/app'
import { runtimeRequest } from '@/lib/http'
import { cn } from '@/lib/utils'
import { addHookTemplate, hookEvents, hookPath, hookStatuses, hookTemplates, readHookDraft, writeHookDraft, type HookEvent, type HookRow, type HookSnapshot } from '@/lib/projectHooks'
import { readHookForm } from '@/lib/hookForm'
import { HookFormEditor } from './HookFormEditor'
import { usePageVisible } from '@/hooks/usePageVisible'

const field = 'w-full min-w-0 rounded-lg border border-line bg-surface px-2.5 py-2 text-xs text-ink focus:border-brand focus:outline-none'

export function ProjectHooksPage() {
  useLocale()

  const workspaces = useAppStore((s) => s.workspaces)
  const active = useAppStore((s) => s.activeWorkspaceId)
  const target = useAppStore((s) => s.settingsWorkspaceRoot)
  const [root, setRoot] = useState(target ?? active ?? workspaces[0]?.id ?? '')
  useEffect(() => { if (target) setRoot(target) }, [target])
  return <div className="space-y-4">
    <div><h3 className="ui-section-title flex items-center gap-2"><GitBranch className="h-5 w-5 text-brand" />{t("ui.components.overlays.ProjectHooksPage.926b04e8ced4")}</h3><p className="mt-1 text-xs leading-relaxed text-muted">{t("ui.components.overlays.ProjectHooksPage.ea5087a0ea65")}</p></div>
    <label className="block space-y-1 text-2xs text-muted">{t("ui.components.overlays.ProjectHooksPage.79f326be4409")}<select className={field} aria-label={t("ui.components.overlays.ProjectHooksPage.7c1a6e48b6dd")} value={root} onChange={(event) => setRoot(event.target.value)}>
      <option value="@user">{t('ui.extensions.userHooks')}</option>
      {root !== '@user' && (!root || !workspaces.some((item) => item.id === root)) ? <option value={root}>{root || t("ui.components.overlays.ProjectHooksPage.fa2c05a0a9cd")}</option> : null}
      {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>
    <HookProject key={root} root={root} />
    <PluginHooksSection />
  </div>
}

function HookProject({ root }: { root: string }) {
  const locale = useLocale()
  const pageVisible = usePageVisible()

  const [data, setData] = useState<HookSnapshot | null>(null)
  const [text, setText] = useState('{"hooks": {}}')
  const [base, setBase] = useState('missing')
  const [savedText, setSavedText] = useState('')
  const [error, setError] = useState<ResourceText>('')
  const [loadError, setLoadError] = useState<ResourceText>('')
  const [notice, setNotice] = useState<ResourceText>('')
  const [draftError, setDraftError] = useState(false)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<'configuration' | 'runs'>('configuration')
  const tabId = useId()
  const [editorMode, setEditorMode] = useState<'form' | 'json'>('form')
  const [event, setEvent] = useState<HookEvent>('PreToolUse')
  const [tool, setTool] = useState('bash')
  const [preview, setPreview] = useState<HookRow[] | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const alive = useRef(true)
  const initial = useRef(true)
  const actionEpoch = useRef(0)
  const actionLock = useRef(false)
  const form = useMemo(() => readHookForm(text), [text, locale])
  const mode = form.document ? editorMode : 'json'
  const invalidForm = mode === 'form' && form.issues.length > 0
  const dirty = text !== savedText
  const changed = data && base !== data.fingerprint
  const adopt = (state: HookSnapshot) => {
    const source = state.text ?? JSON.stringify(state.document, null, 2)
    setData(state); setText(source); setSavedText(source); setBase(state.fingerprint); setPreview(null)
  }
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => {
    if (!pageVisible) return
    const controller = new AbortController()
    let pending = false
    const load = async () => {
      if (actionLock.current || pending || controller.signal.aborted) return
      pending = true
      const epoch = actionEpoch.current
      try {
        const state = await runtimeRequest<HookSnapshot>(hookPath(root), 'GET', undefined, { signal: controller.signal })
        if (controller.signal.aborted || epoch !== actionEpoch.current) return
        if (initial.current) {
          const draft = readHookDraft(root)
          if (draft) { setData(state); setText(draft.text); setSavedText(draft.savedText); setBase(draft.base); setNotice({key: "ui.components.overlays.ProjectHooksPage.9a8a2cd1ceb2"}) }
          else adopt(state)
          initial.current = false
        } else setData(state)
        setLoadError('')
      } catch (failure) { if (!controller.signal.aborted && epoch === actionEpoch.current) setLoadError(failureText(failure)) }
      finally { pending = false }
    }
    void load()
    const timer = setInterval(() => { if (!document.hidden) void load() }, 3000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [root, pageVisible])
  useEffect(() => {
    if (!data || initial.current) return
    setDraftError(!writeHookDraft(root, dirty ? { text, base, savedText } : null))
  }, [root, data, text, base, savedText, dirty])
  const perform = async (action: () => Promise<void>) => {
    if (actionLock.current) return
    actionLock.current = true
    actionEpoch.current += 1
    setBusy(true); setError(''); setNotice('')
    try { await action() } catch (failure) { if (alive.current) setError(failureText(failure)) }
    finally { actionLock.current = false; if (alive.current) setBusy(false) }
  }
  const changeText = (next: string) => { setText(next); setPreview(null); setNotice(''); setError('') }
  const validDocument = () => {
    try { return JSON.parse(text) } catch { throw new ResourceError({key: 'ui.components.overlays.ProjectHooksPage.f0ff6a42f8b7'}) }
  }
  return <div className="space-y-3" aria-label={t("ui.components.overlays.ProjectHooksPage.e4897319d090")}>
    <div className="rounded-xl border border-line bg-raised/50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><span className="flex items-center gap-1.5 text-xs font-medium"><ShieldCheck className="h-4 w-4" />{data?.error ? t("ui.components.overlays.ProjectHooksPage.704165f0aba2") : data?.trusted ? dirty ? t("ui.components.overlays.ProjectHooksPage.6cf086453ec2") : t("ui.components.overlays.ProjectHooksPage.01b5991f238c") : data?.rows.length ? t("ui.components.overlays.ProjectHooksPage.c8c93719b18f") : t("ui.components.overlays.ProjectHooksPage.4de3e4881a8d")}</span>
        <RefreshButton size="xs" variant="ghost" label={t("ui.components.overlays.ProjectHooksPage.33200c548f4d")} disabled={busy || dirty} onClick={() => void perform(async () => { const state = await runtimeRequest<HookSnapshot>(hookPath(root)); if (alive.current) { adopt(state); setNotice({key: "ui.components.overlays.ProjectHooksPage.c23fa8bef6fc"}) } })} /></div>
      <p className="mt-2 break-all font-mono text-2xs text-faint">{data?.source ?? t("ui.components.overlays.ProjectHooksPage.8ef5081560e9")}</p>
      <p className="mt-2 text-2xs leading-relaxed text-muted">{t("ui.components.overlays.ProjectHooksPage.817df5243658")}</p>
    </div>
    {loadError || tab === 'runs' && error ? <p role="alert" className="whitespace-pre-wrap break-words text-xs text-danger">{noticeText(loadError || error)}</p> : null}
    {tab === 'runs' && notice ? <p role="status" className="text-xs text-ok">{noticeText(notice)}</p> : null}
    {draftError ? <p role="alert" className="text-xs text-warn">{t("ui.components.overlays.ProjectHooksPage.40d1b7612d4d")}</p> : null}
    {changed ? <p role="alert" className="rounded-lg bg-warn/10 p-2 text-xs text-warn">{t("ui.components.overlays.ProjectHooksPage.7a1a8fa8c975")}</p> : null}
    <div role="tablist" onKeyDown={handleTablistKeyDown} className="flex gap-4 border-b border-line" aria-label={t("ui.components.overlays.ProjectHooksPage.86f559da1f43")}>{(['configuration', 'runs'] as const).map((value) => <button key={value} type="button" role="tab" id={tabId + '-' + value} aria-controls={tabId + '-panel'} aria-selected={tab === value} tabIndex={tab === value ? 0 : -1} className={cn('border-b-2 pb-2 text-xs', tab === value ? 'border-brand font-medium text-brand' : 'border-transparent text-muted')} onClick={() => setTab(value)}>{value === 'configuration' ? t("ui.components.overlays.ProjectHooksPage.e6e947abb969") : t("ui.components.overlays.ProjectHooksPage.6be3020c8daa", {p0: (data?.runs.length ?? 0)})}</button>)}</div>
    <div role="tabpanel" id={tabId + '-panel'} aria-labelledby={tabId + '-' + tab} className="space-y-3">
    {tab === 'configuration' ? <>
      {data?.error ? <p role="alert" className="break-words text-xs text-danger">{data.error}</p> : null}
      <div><h4 className="text-xs font-medium">{t("ui.components.overlays.ProjectHooksPage.63c93c775f06")}</h4><div className="mt-2 flex flex-wrap gap-1.5">{hookTemplates.map((template, index) => <Button key={template.label} size="xs" disabled={busy || !data || Boolean(form.document && form.commands >= 32)} onClick={() => { try { changeText(addHookTemplate(text, index)); setNotice({key: "ui.components.overlays.ProjectHooksPage.a6783375cace"}) } catch (failure) { setError(failureText(failure)) } }}>{template.label}</Button>)}</div><p className="mt-2 text-2xs text-faint">{t("ui.components.overlays.ProjectHooksPage.142d94b4839b")}</p></div>
      <div role="group" aria-label={t("ui.components.overlays.ProjectHooksPage.c83caeea4de3")} className="flex flex-wrap items-center gap-1"><Button size="sm" active={mode === 'form'} aria-pressed={mode === 'form'} disabled={!form.document || busy} onClick={() => setEditorMode('form')}>{t("ui.components.overlays.ProjectHooksPage.a2a10c1119f6")}</Button><Button size="sm" active={mode === 'json'} aria-pressed={mode === 'json'} disabled={busy} onClick={() => setEditorMode('json')}>{t("ui.components.overlays.ProjectHooksPage.6e814135ad1b")}</Button><span className="ml-1 text-2xs text-faint">{t("ui.components.overlays.ProjectHooksPage.1092f6ef1e57")}</span></div>
      <div data-hook-save-bar className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface/95 p-2 backdrop-blur"><Button size="sm" variant="primary" disabled={busy || !data || !dirty || Boolean(changed) || invalidForm} onClick={() => void perform(async () => { const state = await runtimeRequest<HookSnapshot>(hookPath(root), 'POST', { document: validDocument(), expected_fingerprint: base }); if (alive.current) { adopt(state); setNotice({key: "ui.components.overlays.ProjectHooksPage.8254d45dfafe"}) } })}>{t("ui.components.overlays.ProjectHooksPage.6e584e3d5ce6")}</Button>
        <Button size="sm" disabled={busy || !data || !dirty} onClick={() => { if (data) adopt(data); setError(''); setNotice({key: "ui.components.overlays.ProjectHooksPage.eaf33fdb5df6"}) }}>{t("ui.components.overlays.ProjectHooksPage.b5a929dae5ba")}</Button><span className="text-2xs text-faint">{busy ? t("ui.components.overlays.ProjectHooksPage.574ec7517de1") : dirty ? t("ui.components.overlays.ProjectHooksPage.3c1cb97abcfa") : t("ui.components.overlays.ProjectHooksPage.e460bfa3e05e")}</span>{notice ? <p role="status" className="w-full text-2xs text-ok">{noticeText(notice)}</p> : null}{error ? <p role="alert" className="w-full whitespace-pre-wrap break-words text-xs text-danger">{noticeText(error)}</p> : null}</div>
      {mode === 'form' ? <HookFormEditor text={text} form={form} disabled={busy || !data} onChange={changeText} /> : <>
        {form.error ? <p role="alert" className="text-xs text-warn">{t('ui.hooks.formError', {reason: noticeText(form.error)})}</p> : null}
        <label className="block space-y-1"><span className="text-xs font-medium">{t("ui.components.overlays.ProjectHooksPage.bcb5a89bfebf")}</span><textarea aria-label={t("ui.components.overlays.ProjectHooksPage.bcb5a89bfebf")} spellCheck={false} disabled={busy || !data} className={cn(field, 'min-h-60 resize-y font-mono text-xs leading-relaxed')} value={text} onChange={(e) => changeText(e.target.value)} /></label>
      </>}
      {invalidForm ? <p role="alert" className="text-xs text-danger">{t('ui.hooks.formIssues', {count: form.issues.length})}</p> : null}

      <section className="space-y-2 rounded-xl border border-line p-3" aria-label={t("ui.components.overlays.ProjectHooksPage.68b23ff3b020")}><h4 className="text-xs font-medium">{t("ui.components.overlays.ProjectHooksPage.68b23ff3b020")}</h4><p className="text-2xs text-muted">{event === 'Stop' ? t("ui.components.overlays.ProjectHooksPage.69322b42d3f9") : t("ui.components.overlays.ProjectHooksPage.968fa80c90df")}</p><div className="flex flex-wrap gap-2"><select aria-label={t("ui.components.overlays.ProjectHooksPage.ecef9ff8a6eb")} disabled={busy || !data} className={cn(field, 'w-auto max-w-full')} value={event} onChange={(e) => { setEvent(e.target.value as HookEvent); setPreview(null) }}>{hookEvents.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select><input aria-label={t("ui.components.overlays.ProjectHooksPage.75d7e263bfcd")} disabled={busy || !data || event === 'Stop'} className={cn(field, 'flex-1 basis-28')} value={event === 'Stop' ? t("ui.components.overlays.ProjectHooksPage.48b31bab6b4d") : tool} onChange={(e) => { setTool(e.target.value); setPreview(null) }} /><Button size="sm" disabled={busy || !data || invalidForm} onClick={() => void perform(async () => { const response = await runtimeRequest<{ matches: HookRow[] }>(hookPath(root, '/preview'), 'POST', { document: validDocument(), event, tool: event === 'Stop' ? '*' : tool }); if (alive.current) setPreview(response.matches) })}>{t("ui.components.overlays.ProjectHooksPage.1fc7620f4533")}</Button></div>
        {preview ? <div role="status" className="space-y-1 text-2xs"><p>{preview.length ? t("ui.components.overlays.ProjectHooksPage.9d0f7d176a74", {p0: (preview.length)}) : t("ui.components.overlays.ProjectHooksPage.703c0eade1ce")}</p>{preview.map((row) => <p className="break-all font-mono text-muted" key={row.id}>{row.command}</p>)}</div> : null}</section>
      <section className="space-y-2" aria-label={t("ui.components.overlays.ProjectHooksPage.48516d1aa6df")}><h4 className="text-xs font-medium">{t('ui.hooks.ruleCount', {count: data?.rows.length ?? 0})}</h4>{data?.rows.map((row) => <article key={row.id} className="rounded-lg border border-line p-2.5"><div className="flex flex-wrap items-center gap-1.5 text-2xs"><Terminal className="h-3 w-3" /><span className="font-medium">{row.label}</span><span className="text-faint">{row.enabled ? t(row.event === 'Stop' ? 'ui.hooks.ruleSummary' : 'ui.hooks.matchedRuleSummary', {event: hookEvents.find((item) => item.value === row.event)?.label ?? row.event, matcher: row.matcher, seconds: row.timeout}) : t("ui.components.overlays.ProjectHooksPage.bc5a87a757a5")}</span></div><p className="mt-1.5 whitespace-pre-wrap break-all font-mono text-xs text-muted">{row.command}</p></article>)}
        <p className="text-2xs leading-relaxed text-muted">{t("ui.components.overlays.ProjectHooksPage.42205b86808d")}</p>
        <Button size="sm" disabled={busy || !data || dirty || Boolean(changed) || Boolean(data?.error) || !data?.rows.length} variant={data?.trusted ? 'ghost' : 'primary'} onClick={() => void perform(async () => { if (!data) return; const state = await runtimeRequest<HookSnapshot>(hookPath(root, '/trust'), 'POST', { expected_fingerprint: base, trusted: !data.trusted }); if (alive.current) { setData(state); setNotice({key: state.trusted ? "ui.components.overlays.ProjectHooksPage.01b5991f238c" : "ui.components.overlays.ProjectHooksPage.fb4552981d08"}) } })}>{data?.trusted ? t("ui.components.overlays.ProjectHooksPage.bdaddc7db0c0") : t("ui.components.overlays.ProjectHooksPage.17b552e3abe4")}</Button>
      </section>
    </> : <div className="space-y-2">
      <RefreshButton size="xs" label={t("ui.components.overlays.ProjectHooksPage.30456025b1e3")} disabled={busy} onClick={() => void perform(async () => { const state = await runtimeRequest<HookSnapshot>(hookPath(root)); if (alive.current) setData(state) })} />
      {!data?.runs.length ? <p className="py-5 text-center text-xs text-muted">{t("ui.components.overlays.ProjectHooksPage.a6d4589377d8")}</p> : null}
      {data?.runs.map((run) => <article key={run.id} className="rounded-lg border border-line"><button className="w-full space-y-1 p-3 text-left" type="button" aria-expanded={expanded === run.id} onClick={() => setExpanded(expanded === run.id ? null : run.id)}><div className="flex items-center justify-between gap-2 text-xs"><span className="min-w-0 break-words font-medium">{run.label}</span><span className={cn('shrink-0', run.status === 'passed' ? 'text-ok' : ['failed', 'timed_out', 'cleanup_failed'].includes(run.status) ? 'text-danger' : 'text-muted')}>{hookStatuses[run.status] ?? run.status}</span></div><p className="text-2xs text-faint">{t(run.tool ? 'ui.hooks.runWithTool' : 'ui.hooks.runSummary', {event: run.event, tool: run.tool, milliseconds: run.duration_ms, time: new Date(run.started * 1000).toLocaleTimeString(getLocale())})}</p></button>{expanded === run.id ? <div className="space-y-2 border-t border-line p-3 text-2xs"><p className="break-words text-muted">{run.reason || t("ui.components.overlays.ProjectHooksPage.7483fc75712e")}</p><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-raised p-2 font-mono">{run.stdout || run.stderr ? [run.stdout && `stdout\n${run.stdout}`, run.stderr && `stderr\n${run.stderr}`].filter(Boolean).join('\n\n') : t("ui.components.overlays.ProjectHooksPage.6bc958f11775")}{run.stdout_dropped || run.stderr_dropped ? t("ui.components.overlays.ProjectHooksPage.e21b7eb67507") : ''}</pre></div> : null}</article>)}
      <p className="text-2xs text-faint">{t("ui.components.overlays.ProjectHooksPage.6a2fee841cdb")}</p>
    </div>}
    </div>
  </div>
}
