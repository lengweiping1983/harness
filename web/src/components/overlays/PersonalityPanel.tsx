import type { ResourceText } from '@/types'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { handleTablistKeyDown } from '@/lib/tabNavigation'
import { AgentAvatar } from '@/components/ui/AgentAvatar'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Sparkles, X } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { ApiError, isUnconfirmedWrite, runtimeRequest } from '@/lib/http'
import type { PersonalitySelection } from '@/lib/protocol.generated'
import { isSelectedPersonality } from '@/lib/personalitySelection'
import { listPersonalityDrafts, personalityDraftKey, persistPersonalityDraft, type PersonalityDraft } from '@/lib/personalityDraft'
import { Badge, Button, Overlay, RefreshButton } from '@/components/ui/primitives'

import type { PersonalityProfile as Profile } from '@/lib/protocol.generated'
const AXES = [['reflective', 'proactive'], ['conceptual', 'concrete'], ['analytical', 'empathetic'], ['structured', 'adaptive']]

export function PersonalityPanel() {
  const locale = useLocale()
  const [current, setCurrent] = useState<PersonalitySelection | null>(null)
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [draft, setDraft] = useState('')
  const [draftCode, setDraftCode] = useState<string | null>(null)
  const [choices, setChoices] = useState<string[]>(Array(AXES.length).fill(''))
  const quizComplete = AXES.every((axis, index) => axis.includes(choices[index]))
  const [recommendation, setRecommendation] = useState<string | null>(null)
  const [actionError, setError] = useState<ResourceText>('')
  const [readError, setReadError] = useState<ResourceText>('')
  const error: ResourceText = actionError && readError ? {template: {key: 'ui.common.feedbackPair', params: {first: actionError, second: readError}}} : actionError || readError
  const [notice, setNotice] = useState<ResourceText>('')
  const [draftStorageError, setDraftStorageError] = useState(false)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const generation = useRef(0)
  const actionLock = useRef(false)
  const reading = useRef(false)
  const alive = useRef(true)
  const refreshAfterAction = useRef(false)
  const loadAbort = useRef<AbortController | null>(null)
  const currentLoad = useRef<(() => Promise<void>) | null>(null)
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  const [source, setSource] = useState<'user' | 'builtin' | 'all'>('all')
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<Profile | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [editorOpen, setEditorOpen] = useState(false)
  const [viewing, setViewing] = useState<Profile | null>(null)
  const detailTitleId = useId()
  const titleId = useId()
  const [draftKey, setDraftKey] = useState(personalityDraftKey('new'))
  const draftCache = useRef(new Map<string, PersonalityDraft>())
  const draftBaseline = useRef('')
  const [draftEntries, setDraftEntries] = useState<[string, PersonalityDraft][]>([])
  const loadedKey = useRef<string | null>(null)
  const load = useCallback(async () => {
    if (actionLock.current) {refreshAfterAction.current = true; return}
    loadAbort.current?.abort()
    const controller = new AbortController()
    loadAbort.current = controller
    const options = {signal: controller.signal}
    const version = ++generation.current
    reading.current = true
    setReadError('')
    setLoading(true)
    try {
      setCurrent(null)
      const [catalog, personality] = await Promise.allSettled([runtimeRequest<{ profiles: Profile[]; warning?: string }>('/personalities', 'GET', undefined, options), runtimeRequest<PersonalitySelection>('/personality-selection', 'GET', undefined, options)])
      if (generation.current === version) {
        if (loadedKey.current === null) {
          try { draftCache.current = listPersonalityDrafts(sessionStorage); setDraftStorageError(false) }
          catch { setDraftStorageError(true) }
          setDraftEntries([...draftCache.current])
          loadedKey.current = ''
        }
        if (personality.status === 'fulfilled') setCurrent(personality.value)
        if (catalog.status === 'fulfilled') setProfiles(catalog.value.profiles)
        const issues: ResourceText[] = [catalog.status === 'fulfilled' ? catalog.value.warning ?? '' : failureText(catalog.reason),
          personality.status === 'rejected' ? failureText(personality.reason) : ''].filter(Boolean)
        const issue: ResourceText = issues.length > 1 ? {template: {key: 'ui.common.feedbackPair', params: {first: issues[0]!, second: issues[1]!}}} : issues[0] ?? ''
        setReadError(issue)
      }
    } catch (e) { if (generation.current === version) setReadError(failureText(e)) }
    finally { if (generation.current === version) { reading.current = false; setLoading(false) } }
  }, [locale])
  currentLoad.current = load
  useEffect(() => {
    void load()
    return () => { generation.current++; reading.current = false; loadAbort.current?.abort() }
  }, [load])
  const beginOperation = () => {
    if (!alive.current || reading.current || actionLock.current) return false
    actionLock.current = true
    generation.current++
    loadAbort.current?.abort()
    setBusy(true); setError(''); setNotice('')
    return true
  }
  const finishOperation = () => {
    actionLock.current = false
    if (!alive.current) return
    setBusy(false)
    if (refreshAfterAction.current) {
      refreshAfterAction.current = false
      void currentLoad.current?.()
    }
  }
  const writeFailure = (failure: unknown): ResourceText => {
    if (!(failure instanceof ApiError) || isUnconfirmedWrite(failure.failure)) {
      refreshAfterAction.current = true
      return {template: {key: 'ui.personality.writeUnknown', params: {reason: failureText(failure)}}}
    }
    return failureText(failure)
  }
  const retainDraft = () => {
    if (loadedKey.current !== draftKey) return
    const value: PersonalityDraft = { name, description, prompt: draft, code: draftCode, editing: editing ? { id: editing.id, revision: editing.revision!, name: editing.name } : null }
    const empty = (!name && !description && !draft && !draftCode && !editing) || JSON.stringify(value) === draftBaseline.current
    if (empty) draftCache.current.delete(draftKey)
    else draftCache.current.set(draftKey, value)
    setDraftEntries([...draftCache.current])
    try { setDraftStorageError(!persistPersonalityDraft(sessionStorage, draftKey, empty ? null : value)) }
    catch { setDraftStorageError(true) }
  }
  useEffect(() => {
    if (loading) return
    retainDraft()
  }, [draftKey, loading, name, description, draft, draftCode, editing])
  const clearEditorDraft = () => {
    try {
      if (!persistPersonalityDraft(sessionStorage, draftKey, null)) { setDraftStorageError(true); return false }
      loadedKey.current = draftKey; setDraftStorageError(false)
    } catch { setDraftStorageError(true); return false }
    draftCache.current.delete(draftKey); setDraftEntries([...draftCache.current])
    setName(''); setDescription(''); setDraft(''); setDraftCode(null); setEditing(null)
    setConfirmDelete(false); setEditorOpen(false)
    return true
  }
  const openProfile = (profile: Profile | null) => {
    retainDraft()
    const key = personalityDraftKey(profile?.source === 'user' ? 'edit:' + profile.id : profile ? 'copy:' + profile.id : 'new')
    const recovered = draftCache.current.get(key)
    draftBaseline.current = JSON.stringify({ name: profile?.name ?? '', description: profile?.description ?? '', prompt: profile?.prompt ?? '', code: profile?.code ?? null, editing: profile?.source === 'user' ? { id: profile.id, revision: profile.revision, name: profile.name } : null })
    loadedKey.current = key; setDraftKey(key)
    setConfirmDelete(false)
    setEditing(recovered?.editing ? { ...recovered.editing, source: 'user', code: null, prompt: recovered.prompt, description: recovered.description } : recovered ? null : profile?.source === 'user' ? profile : null)
    setName(recovered?.name ?? profile?.name ?? ''); setDescription(recovered?.description ?? profile?.description ?? '')
    setDraftCode(recovered?.code ?? profile?.code ?? null); setDraft(recovered?.prompt ?? profile?.prompt ?? '')
    setError(''); setNotice(recovered ? {key: 'ui.personality.draftRestored'} : '')
    setEditorOpen(true)
  }
  const resumeDraft = (key: string, recovered: PersonalityDraft) => {
    retainDraft()
    const original = profiles.find(profile => recovered.editing ? profile.id === recovered.editing.id : recovered.code && profile.code === recovered.code)
    draftBaseline.current = original ? JSON.stringify({ name: original.name, description: original.description, prompt: original.prompt, code: original.code, editing: original.source === 'user' ? { id: original.id, revision: original.revision, name: original.name } : null }) : ''
    loadedKey.current = key; setDraftKey(key)
    setEditing(recovered.editing ? { ...recovered.editing, source: 'user', code: null, prompt: recovered.prompt, description: recovered.description } : null)
    setName(recovered.name); setDescription(recovered.description); setDraft(recovered.prompt); setDraftCode(recovered.code)
    setConfirmDelete(false); setError(''); setNotice({key: 'ui.personality.draftRestored'}); setEditorOpen(true)
  }
  const duplicateDraft = () => {
    retainDraft()
    const key = personalityDraftKey('copy:' + crypto.randomUUID())
    draftBaseline.current = ''
    loadedKey.current = key; setDraftKey(key)
    setEditing(null); setConfirmDelete(false); setName(t('ui.personality.copyName', { name }).slice(0, 120)); setError(''); setNotice('')
  }
  const saveProfile = async (useAfterSave = false) => {
    if (useAfterSave && !current) return
    if (!beginOperation()) return
    try {
      const result = await runtimeRequest<Profile>('/personalities', 'POST', { id: editing?.id, revision: editing?.revision, name, description, prompt: draft })
      if (alive.current) {
        setProfiles(previous => [...previous.filter(row => row.id !== result.id), result]); setEditing(result); setSource('user'); setNotice({key: 'ui.personality.librarySaved'})
        if (useAfterSave && current) {
          try {
            const selected = await runtimeRequest<PersonalitySelection>('/personality-selection', 'PATCH', { profile_id: result.id, expected_revision: current.revision })
            if (!alive.current) return
            setCurrent(selected); setNotice({key: 'ui.personality.globalSaved'})
          } catch (failure) {
            if (!alive.current) return
            setError({template: {key: 'ui.personality.savedNotApplied', params: {name: result.name, reason: writeFailure(failure)}}})
          }
        }
        clearEditorDraft()
      }
    } catch (e) { if (alive.current) setError(writeFailure(e)) }
    finally { finishOperation() }
  }
  const deleteProfile = async () => {
    if (!editing) return
    if (!beginOperation()) return
    try {
      await runtimeRequest('/personalities/' + encodeURIComponent(editing.id), 'DELETE', { revision: editing.revision })
      if (alive.current) {
        setProfiles(previous => previous.filter(row => row.id !== editing.id)); if (clearEditorDraft()) setNotice({key: 'ui.personality.deleted'}); else setError({key: 'ui.agent.author.draftCleanupFailed'})
      }
    } catch (e) { if (alive.current) setError(writeFailure(e)) }
    finally { finishOperation() }
  }
  const suggest = async () => {
    if (!quizComplete) return
    if (!beginOperation()) return
    try {
      const result = await runtimeRequest<{ code: string }>('/personalities/recommend', 'POST', { choices })
      if (alive.current) setRecommendation(result.code)
    }
    catch (e) { if (alive.current) setError(failureText(e)) }
    finally { finishOperation() }
  }
  const selectProfile = async (id: string) => {
    if (!current) return false
    const profile = profiles.find(row => row.id === id)
    if (id && !profile) return false
    if (!beginOperation()) return false
    try {
      const result = await runtimeRequest<PersonalitySelection>('/personality-selection', 'PATCH', {
        profile_id: profile?.id ?? null, expected_revision: current.revision,
      })
      if (alive.current) { setCurrent(result); setNotice({key: 'ui.personality.globalSaved'}); return true }
      return false
    } catch (e) { if (alive.current) setError(writeFailure(e)); return false }
    finally { finishOperation() }
  }
  const selectedProfile = current?.profile
  const suggested = profiles.find(profile => profile.code === recommendation)
  const viewedProfile = viewing?.source === 'builtin' ? profiles.find(profile => profile.id === viewing.id) ?? viewing : viewing
  const profileFields = (profile?: Profile) => <div className="space-y-4">
    <label className="block space-y-2 text-sm">{t('ui.personality.name')}<input readOnly={!!profile} value={profile?.name ?? name} maxLength={120} disabled={!profile && (busy || loading)} onChange={event => setName(event.target.value)} className="w-full rounded-lg border border-line bg-canvas px-3 py-2" /></label>
    <label className="block space-y-2 text-sm">{t('ui.personality.summary')}<input readOnly={!!profile} value={profile?.description ?? description} maxLength={500} disabled={!profile && (busy || loading)} onChange={event => setDescription(event.target.value)} className="w-full rounded-lg border border-line bg-canvas px-3 py-2" /></label>
    {!profile && <p className="text-xs leading-relaxed text-muted">{t('ui.personality.editorHint')}</p>}
    <label className="block space-y-2 text-sm">{t('ui.personality.content')}<textarea readOnly={!!profile} aria-label={t('ui.personality.content')} disabled={!profile && (busy || loading)} maxLength={1000} value={profile?.prompt ?? draft} onChange={event => setDraft(event.target.value)} className="min-h-56 w-full resize-y rounded-lg border border-line bg-canvas px-3 py-2 text-sm leading-relaxed" /></label>
  </div>
  return <section className="refresh-action-container space-y-4" aria-label={t('ui.personality.heading')}>
    <div className="flex items-start justify-between gap-3"><div><h3 className="ui-section-title">{t('ui.personality.heading')}</h3><p className="mt-1 text-xs text-muted">{t('ui.personality.description')}</p></div><RefreshButton size="sm" label={t('ui.personality.refresh')} disabled={busy || loading} onClick={() => void load()} /></div>
    {!editorOpen && draftStorageError && <p role="alert" className="text-sm text-danger">{t('ui.agent.author.draftUnavailable')}</p>}
    {!editorOpen && error && <p role="alert" className="text-sm text-danger">{noticeText(error)}</p>}
    {!editorOpen && notice && <p role="status" className="text-xs text-brand">{noticeText(notice)}</p>}
    {loading && <p role="status" className="text-xs text-muted">{t('ui.personality.loading')}</p>}
    <section aria-label={t('ui.personality.globalSelect')} className="rounded-xl border border-line bg-raised/60 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><h4 className="break-words font-medium">{loading ? t('ui.personality.loading') : selectedProfile?.name || t('ui.personality.unselected')}</h4></div>
        {selectedProfile && <Button size="sm" variant="ghost" disabled={busy || loading} onClick={() => void selectProfile('')}>{t('ui.personality.clearSelection')}</Button>}
      </div>
      {selectedProfile?.description && <p className="mt-2 break-words text-sm text-muted">{selectedProfile.description}</p>}
      <p className="mt-2 text-xs leading-relaxed text-muted">{t(selectedProfile ? 'ui.personality.globalHint' : 'ui.personality.pickHint')}</p>
      {selectedProfile && <details className="mt-3 border-t border-line pt-3"><summary className="cursor-pointer text-sm text-brand">{t('ui.personality.fullDetails')}</summary><pre className="mt-3 max-h-80 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-canvas p-3 font-sans text-sm leading-relaxed">{selectedProfile.prompt}</pre></details>}
    </section>
    <details className="rounded-lg border border-line p-3"><summary className="cursor-pointer text-sm font-medium">{t('ui.personality.quiz')}</summary>
      <p className="my-3 text-xs text-muted">{t('ui.personality.quizHint')}</p>
      <div className="grid gap-4 sm:grid-cols-2">{AXES.map((axis, index) => <fieldset key={index} disabled={busy} className="space-y-2"><legend className="text-xs font-medium">{t(`ui.personality.axis${index}`)}</legend>{axis.map(letter => <label key={letter} className="flex cursor-pointer items-start gap-2 text-xs text-muted"><input className="mt-0.5" type="radio" name={`personality-axis-${index}`} value={letter} checked={choices[index] === letter} onChange={() => { setChoices(previous => previous.map((value, position) => position === index ? letter : value)); setRecommendation(null) }} />{t('ui.personality.' + letter)}</label>)}</fieldset>)}</div>
      {!quizComplete ? <p role="status" className="mt-3 text-xs text-muted">{t('ui.personality.quizIncomplete')}</p> : null}
      <Button className="mt-4" size="sm" disabled={busy || !quizComplete} icon={<Sparkles className="h-3.5 w-3.5" />} onClick={() => void suggest()}>{t('ui.personality.recommend')}</Button>
      {suggested && <div role="status" className="mt-3 space-y-2 rounded-lg bg-raised p-3"><p className="text-sm font-medium">{suggested.name}</p><p className="text-xs text-muted">{suggested.description}</p><Button variant="primary" size="sm" disabled={busy || loading} onClick={() => void selectProfile(suggested.id)}>{t('ui.personality.apply')}</Button></div>}
    </details>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div role="tablist" onKeyDown={handleTablistKeyDown} aria-label={t('ui.personality.catalog')} className="flex gap-1 rounded-lg bg-raised p-1">{(['user', 'builtin', 'all'] as const).map(value => <button key={value} type="button" role="tab" tabIndex={source === value ? 0 : -1} aria-selected={source === value} onClick={() => setSource(value)} className={`rounded-md px-3 py-2 text-sm ${source === value ? 'bg-canvas font-medium shadow-sm' : 'text-muted'}`}>{t('ui.personality.tab.' + value)}</button>)}</div>
      <div className="flex flex-wrap gap-2"><Button size="sm" disabled={busy || loading} onClick={() => openProfile(null)}>{t('ui.personality.create')}</Button></div>
    </div>
    {draftEntries.length > 0 && <section aria-label={t('ui.personality.pendingDrafts')} className="space-y-2 rounded-xl border border-line bg-raised/40 p-3">
      <p className="text-xs font-medium text-muted">{t('ui.personality.pendingDrafts')}</p>
      {draftEntries.map(([key, saved]) => <div key={key} className="flex flex-wrap items-center justify-between gap-2">
        <span className="min-w-0 break-words text-sm">{saved.name || t('ui.personality.unnamedDraft')}</span>
        <Button size="sm" variant="secondary" disabled={busy || loading} aria-label={t('ui.personality.resumeNamedDraft', { name: saved.name || t('ui.personality.unnamedDraft') })} onClick={() => resumeDraft(key, saved)}>{t('ui.personality.continueDraft')}</Button>
      </div>)}
    </section>}
    <input aria-label={t('ui.personality.search')} placeholder={t('ui.personality.search')} value={search} onChange={event => setSearch(event.target.value)} className="w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm" />
    <p className="text-xs text-muted">{t('ui.personality.libraryHint')}</p>
    <div className="grid gap-3 sm:grid-cols-2">{profiles.filter(profile => (source === 'all' || profile.source === source) && `${profile.name} ${profile.description} ${profile.code ?? ''}`.toLowerCase().includes(search.toLowerCase())).map(profile => <article key={profile.id} className={`flex flex-col rounded-xl border p-4 ${isSelectedPersonality(profile, selectedProfile) ? 'border-brand bg-brand/5' : 'border-line'}`}>
      <div className="flex flex-wrap items-center gap-2 text-sm font-medium"><AgentAvatar className="mr-1 h-10 w-10 shrink-0" /><span className="min-w-0 break-words">{profile.name}</span>{profile.source === 'builtin' && <Badge className="shrink-0">{t('ui.personality.tab.builtin')}</Badge>}</div>
      <p className="mt-2 text-xs leading-relaxed text-muted">{profile.description || t('ui.personality.noDescription')}</p>
      <div className="mt-auto flex flex-wrap items-center gap-2 pt-4">
        <Button size="sm" variant="secondary" disabled={busy || loading || !current || isSelectedPersonality(profile, selectedProfile)} onClick={() => void selectProfile(profile.id)}>{t(isSelectedPersonality(profile, selectedProfile) ? 'ui.personality.selected' : 'ui.personality.apply')}</Button>
        <Button size="sm" variant="ghost" disabled={busy || loading} onClick={() => { setViewing(profile); setError(''); setNotice('') }}>{t('ui.personality.viewDetails')}</Button>
      </div>
    </article>)}</div>
    {!profiles.some(profile => (source === 'all' || profile.source === source) && `${profile.name} ${profile.description} ${profile.code ?? ''}`.toLowerCase().includes(search.toLowerCase())) && <p className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">{t('ui.personality.empty')}</p>}
    {viewedProfile && <Overlay labelledBy={detailTitleId} onClose={() => { if (!busy) setViewing(null) }} className="flex max-h-dialog max-w-3xl flex-col sm:max-h-dialog-wide">
      <header className="flex shrink-0 items-start justify-between gap-3 border-b border-line px-5 py-4"><div className="min-w-0"><h3 id={detailTitleId} className="break-words font-medium">{viewedProfile.name}</h3><p className="mt-1 break-words text-sm text-muted">{viewedProfile.description}</p></div><Button size="sm" variant="ghost" disabled={busy} aria-label={t('ui.personality.closeDetails')} onClick={() => setViewing(null)}><X className="h-4 w-4" /></Button></header>
      <div className="min-h-0 space-y-4 overflow-y-auto p-5">{error && <p role="alert" className="text-sm text-danger">{noticeText(error)}</p>}{profileFields(viewedProfile)}</div>
      <footer className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-4"><Button size="sm" variant="secondary" disabled={busy || loading || !current || isSelectedPersonality(viewedProfile, selectedProfile)} onClick={() => void selectProfile(viewedProfile.id).then(done => { if (done) setViewing(null) })}>{t(isSelectedPersonality(viewedProfile, selectedProfile) ? 'ui.personality.selected' : 'ui.personality.apply')}</Button><Button size="sm" variant="primary" disabled={busy || loading} onClick={() => { const profile = viewedProfile; setViewing(null); openProfile(profile) }}>{t(viewedProfile.source === 'user' ? 'ui.personality.edit' : 'ui.personality.customize')}</Button></footer>
    </Overlay>}
    {editorOpen && <Overlay labelledBy={titleId} onClose={() => { if (!busy) setEditorOpen(false) }} className="flex max-h-dialog max-w-3xl flex-col sm:max-h-dialog-wide">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-5 py-4">
        <div><h3 id={titleId} className="font-medium">{t(editing ? 'ui.personality.edit' : 'ui.personality.editorTitle')}</h3><p role="status" className="mt-1 text-xs text-muted">{draftStorageError ? t('ui.agent.author.draftNotRetained') : notice ? noticeText(notice) : t('ui.personality.draftHint')}</p></div>
        <Button size="sm" variant="ghost" disabled={busy} aria-label={t('ui.personality.closeEditor')} onClick={() => setEditorOpen(false)}><X className="h-4 w-4" /></Button>
      </header>
      <div className="min-h-0 space-y-4 overflow-y-auto p-5">
        {draftStorageError && <p role="alert" className="text-sm text-danger">{t('ui.agent.author.draftUnavailable')}</p>}
        {error && <p role="alert" className="text-sm text-danger">{noticeText(error)}</p>}
      {profileFields()}

      </div>
      <footer className="max-h-composer-footer shrink-0 space-y-3 overflow-y-auto border-t border-line px-5 py-4">
        <div className="flex flex-wrap items-center justify-end gap-2">
          <details className="text-xs text-muted"><summary className="inline-flex h-8 cursor-pointer items-center gap-2 rounded-lg px-2.5 font-medium hover:bg-raised hover:text-ink">{t('ui.personality.moreActions')}</summary><div className="mt-2 flex flex-wrap gap-2">
            {editing && <><Button size="sm" variant="secondary" disabled={busy || loading} onClick={duplicateDraft}>{t('ui.personality.duplicate')}</Button>
            <Button size="sm" variant="ghost" disabled={busy || loading} onClick={() => setConfirmDelete(true)}>{t('ui.personality.delete')}</Button></>}
            <Button size="sm" variant="ghost" disabled={busy || loading} onClick={() => { if (clearEditorDraft()) { setError(''); setNotice({key: 'ui.personality.draftDeleted'}) } }}>{t('ui.personality.deleteDraft')}</Button>
          </div></details>
          <Button size="sm" variant="secondary" disabled={busy || loading || !name.trim() || !draft.trim()} onClick={() => void saveProfile()}>{t('ui.personality.save')}</Button>
          <Button size="sm" variant="primary" disabled={busy || loading || !current || !name.trim() || !draft.trim()} onClick={() => void saveProfile(true)}>{t('ui.personality.saveAndUse')}</Button>
        </div>
    {editing && confirmDelete && <div role="alert" className="space-y-3 rounded-lg border border-line bg-raised p-3">
      <p className="text-sm">{t('ui.personality.deleteHint', { name: editing.name })}</p>
      <div className="flex flex-wrap justify-end gap-2"><Button size="sm" disabled={busy} onClick={() => void deleteProfile()}>{t('ui.personality.confirmDelete')}</Button><Button size="sm" variant="secondary" disabled={busy} onClick={() => setConfirmDelete(false)}>{t('ui.personality.cancelDelete')}</Button></div>
    </div>}

      </footer>
    </Overlay>}


  </section>
}
