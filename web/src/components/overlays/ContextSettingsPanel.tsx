import type { ResourceText } from '@/types'
import {Check} from 'lucide-react'
import { noticeText } from '@/lib/noticeText'
import { Switch } from '@/components/ui/primitives'
import {SaveStatus} from '@/components/ui/SaveStatus'
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { t, useLocale, getLocale } from '@/i18n'
import { request } from '@/lib/http'
import { applyContextPreset, contextPresets, contextWindow, selectedContextPreset, type ContextPresetId } from '@/lib/contextPresets'
import {contextPolicyFields, contextPolicySaves, type ContextPolicySnapshot} from '@/lib/contextPolicySave'

import type { ContextManagementSettings as Policy } from '@/lib/protocol.generated'

const fields = ['tool_result_max_chars', 'summary_max_tokens', 'output_reserve', 'context_window'] as const

export function ContextSettingsPanel({modelContextWindow}: {modelContextWindow?: number | null}) {
  useLocale()
  const [rawDraft, setDraft] = useState<Policy | null>(null)
  const [windowSize, setWindowSize] = useState<number | null>(null)
  const [presetId, setPresetId] = useState<ContextPresetId>('balanced')
  const modelWindowRef = useRef(modelContextWindow)
  modelWindowRef.current = modelContextWindow
  const budgetWindow = contextWindow(modelContextWindow === undefined ? windowSize : modelContextWindow, rawDraft?.context_window)
  const presets = contextPresets(budgetWindow, rawDraft?.output_reserve)
  const draft = rawDraft ? {...rawDraft, context_working_budget: presets.find(preset => preset.id === presetId)!.context_working_budget} : null
  function presetFor(policy: Policy, window: number | null) {return selectedContextPreset(policy, contextWindow(modelWindowRef.current === undefined ? window : modelWindowRef.current, policy.context_window))}
  const [saved, setSaved] = useState<Policy | null>(null)
  const [message, setMessage] = useState<ResourceText>('')
  const [error, setError] = useState<ResourceText>('')
  const owner = useRef({})
  const draftRevision = useRef(0)
  const acceptedRevision = useRef(0)
  const operation = useSyncExternalStore(contextPolicySaves.subscribe, contextPolicySaves.getSnapshot)
  const seenOperation = useRef(operation)
  const busy = operation?.phase === 'saving'
  const unknown = operation?.phase === 'unknown'
  const version = useRef(0)
  useEffect(() => {
    const abort = new AbortController()
    const ticket = version.current
    void request<{ config: Policy; effective: { context_window: number | null } }>('/api/v1/settings', { signal: abort.signal }).then(result => {
      if (abort.signal.aborted || ticket !== version.current) return
      if (result.ok) { setDraft(result.data.config); setSaved(result.data.config); setPresetId(presetFor(result.data.config, result.data.effective?.context_window ?? null)); setWindowSize(result.data.effective?.context_window ?? null) }
      else setError(result.copy ?? result.message)
    })
    return () => abort.abort()
  }, [])
  function edit(next: Policy) {
    ++draftRevision.current
    contextPolicySaves.clearResolved()
    setDraft(next); setMessage(''); setError('')
  }
  async function save() {
    setError(''); setMessage('')
    if (contextPolicySaves.getSnapshot()?.phase === 'unknown') await contextPolicySaves.recheck()
    else if (draft) {
      const body = Object.fromEntries(contextPolicyFields.filter(key => draft[key] !== saved?.[key]).map(key => [key, draft[key]]))
      await contextPolicySaves.save(body, owner.current, draftRevision.current)
    }
  }
  const dirty = draft && saved && contextPolicyFields.some(key => draft[key as keyof Policy] !== saved[key as keyof Policy])
  const rawDirty = rawDraft && saved && contextPolicyFields.some(key => rawDraft[key] !== saved[key])
  const dirtyRef = useRef(Boolean(rawDirty))
  dirtyRef.current = Boolean(rawDirty)
  useEffect(() => {
    if (seenOperation.current === operation) return
    seenOperation.current = operation
    if (operation?.phase !== 'saved' || !operation.snapshot) return
    const snapshot = operation.snapshot
    setSaved(snapshot.config); setWindowSize(snapshot.effective?.context_window ?? null)
    const ownDraft = operation.owner === owner.current && operation.draftRevision === draftRevision.current
    if (ownDraft || operation.owner !== owner.current && draftRevision.current === acceptedRevision.current && !dirtyRef.current) {
      acceptedRevision.current = draftRevision.current
      setDraft(snapshot.config)
      if (!ownDraft) setPresetId(presetFor(snapshot.config, snapshot.effective?.context_window ?? null))
    }
    setMessage({key: ownDraft ? 'ui.context.saved' : 'ui.context.savedElsewhere'})
  }, [operation])
  useEffect(() => {
    const observe = (event: Event) => {
      const data = (event as CustomEvent<ContextPolicySnapshot & {context_save_id?: number}>).detail
      ++version.current
      if (data.context_save_id !== undefined && data.context_save_id === contextPolicySaves.getSnapshot()?.id) return
      if (rawDirty || draftRevision.current !== acceptedRevision.current) { setMessage({key: 'ui.context.savedElsewhere'}); return }
      setDraft(data.config); setSaved(data.config); setPresetId(presetFor(data.config, data.effective?.context_window ?? null)); setWindowSize(data.effective?.context_window ?? null)
    }
    window.addEventListener('harness-context-policy-saved', observe)
    return () => window.removeEventListener('harness-context-policy-saved', observe)
  }, [rawDirty])
  return <section className="space-y-3" aria-label={t('ui.context.settings')}>
    <h3 className="ui-section-title">{t('ui.context.settings')}</h3>
    <p className="text-xs leading-relaxed text-muted">{budgetWindow === null ? t('ui.context.presetFallback') : t('ui.context.presetWindow', {count: budgetWindow.toLocaleString(getLocale())})}</p>
    {error || operation?.error ? <p role="alert" className={unknown ? 'text-xs text-warn' : 'text-xs text-danger'}>{noticeText(error || operation?.error || '')}</p> : null}
    {!draft ? !error && <p role="status" className="text-xs text-faint">{t('ui.context.loading')}</p> : <>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3" role="group" aria-label={t('ui.context.presets')}>
        {presets.map(preset => <button key={preset.id} type="button" disabled={busy}
          aria-pressed={presetId === preset.id}
          className={`rounded-lg border px-3 py-2 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:opacity-50 ${presetId === preset.id ? 'border-brand bg-brand/[0.12]' : 'border-line hover:bg-raised'}`}
          onClick={() => {setPresetId(preset.id); edit(applyContextPreset(draft, preset.id, budgetWindow))}}>
          <span className="flex items-center justify-between gap-2 text-xs font-medium">{t(`ui.context.preset.${preset.id}`)}{presetId === preset.id ? <Check aria-hidden className="h-3.5 w-3.5 shrink-0 text-brand" /> : null}</span>
          <span className="mt-1 block text-2xs text-muted">{t(`ui.context.preset.${preset.id}.note`)}</span>
          <span className="mt-1 block text-2xs tabular-nums text-faint">{t(budgetWindow === null ? 'ui.context.workingBudget' : preset.limited ? 'ui.context.presetBudgetLimited' : 'ui.context.presetBudget', {count: preset.context_working_budget.toLocaleString(getLocale()), percent: preset.ratio * 100})}</span>
        </button>)}
      </div>
      {presets.some(preset => preset.limited) ? <p className="text-2xs text-muted">{t('ui.context.presetSafety')}</p> : null}
      <label className="flex items-center gap-2 text-xs"><Switch checked={draft.context_auto_manage} disabled={busy} onChange={e => edit({ ...draft, context_auto_manage: e.target.checked })} />{t('ui.context.auto')}</label>
      <details className="rounded-lg border border-line p-3"><summary className="cursor-pointer text-xs">{t('ui.context.advanced')}</summary><div className="mt-3 space-y-3">
        <p className="text-xs leading-relaxed text-muted">{t('ui.context.policyNote')}</p>
        <p className="text-2xs text-faint">{budgetWindow === null ? t('ui.context.windowUnknown') : t('ui.context.window', {count: budgetWindow.toLocaleString(getLocale())})}</p>
        <label className="block space-y-1 text-xs"><span>{t('ui.context.toolSchemaMode')}</span><select className="ui-field" aria-label={t('ui.context.toolSchemaMode')} value={draft.tool_schema_mode} disabled={busy} onChange={e => edit({ ...draft, tool_schema_mode: e.target.value as Policy['tool_schema_mode'] })}>
          <option value="auto">{t('ui.context.toolSchemaAuto')}</option><option value="all">{t('ui.context.toolSchemaAll')}</option>
        </select></label>
        <p className="text-2xs leading-relaxed text-muted">{t('ui.context.toolSchemaNote')}</p>
        <p className="text-2xs leading-relaxed text-muted">{t('ui.context.resultProjectionNote')}</p>
        {fields.map(key => <label key={key} className="block space-y-1 text-xs"><span>{t(`ui.context.${key}`)}</span><input className="ui-field" type="number" min={key === 'output_reserve' ? 0 : key === 'tool_result_max_chars' ? 512 : 1} value={draft[key] ?? ''} disabled={busy} onChange={e => edit({ ...draft, [key]: e.target.value === '' && ['summary_max_tokens', 'context_window', 'tool_result_max_chars'].includes(key) ? null : Number(e.target.value) })} /></label>)}</div></details>
    </>}
    <p className="text-2xs text-faint">{t('ui.context.scope')}</p>
    {unknown ? <p className="text-xs leading-reading text-warn">{t('ui.context.pendingSave')}</p> : null}
    {draft || operation ? <div className="flex flex-wrap items-center gap-3"><button type="button" className="rounded-lg border border-line px-3 py-1.5 text-xs disabled:opacity-50" disabled={busy || !dirty && !unknown} onClick={() => void save()}>{t(busy ? operation?.action === 'check' ? 'ui.context.checkingSave' : 'ui.context.saving' : unknown ? 'ui.context.recheckSave' : 'ui.context.save')}</button><SaveStatus phase={busy ? 'saving' : unknown ? 'unknown' : operation?.phase === 'failed' ? 'failed' : dirty ? 'unsaved' : 'saved'} /><span role="status" className="text-2xs text-muted">{message ? noticeText(message) : ''}</span></div> : null}
  </section>
}
