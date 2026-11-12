import {useEffect, useRef, useState} from 'react'
import {BookOpen, Sparkles} from 'lucide-react'
import {hasTranslation, t, useLocale} from '@/i18n'
import type {AcademyDesign, AcademyReceipt, ResourceText} from '@/types'
import {academyApi, isAcademyUnconfirmedWrite} from '@/lib/academy'
import {academyDesignPendingKey, academyDesignReceiptMatches, createAcademyTopicDraftStorage, readAcademyDesignPending, watchAcademyDesigns} from '@/lib/academyAuthoring'
import type {AcademyDesignPending, AcademyTopicDraft} from '@/lib/academyAuthoring'
import {noticeText} from '@/lib/noticeText'
import {Button, RefreshButton, Section, Spinner} from '@/components/ui/primitives'

const draftStorage = createAcademyTopicDraftStorage(() => sessionStorage)

export function AcademyTopicStudio({active, disabled, onCatalogChanged, onOpenCourse}: {
  active: boolean; disabled: boolean; onCatalogChanged: () => void; onOpenCourse: (courseId: string) => void
}) {
  useLocale()
  const [initial] = useState(() => {
    const saved = draftStorage.read()
    try {return {...saved, pending: readAcademyDesignPending(sessionStorage), invalid: false}}
    catch {return {...saved, pending: null, invalid: true}}
  })
  const [draft, setDraft] = useState<AcademyTopicDraft>(initial.draft)
  const [draftFailed, setDraftFailed] = useState(initial.failed)
  const [pending, setPending] = useState<AcademyDesignPending | null>(initial.pending)
  const [storageError, setStorageError] = useState(initial.invalid)
  const [designs, setDesigns] = useState<AcademyDesign[]>([])
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [error, setError] = useState<ResourceText>('')
  const lock = useRef(false)
  const composing = useRef(false)
  const mounted = useRef(true)
  const activeRef = useRef(active)
  const epoch = useRef(0)
  if (activeRef.current !== active) {activeRef.current = active; ++epoch.current}
  const recovered = useRef('')
  const announced = useRef(new Set<string>())
  const catalogChanged = useRef(onCatalogChanged)
  catalogChanged.current = onCatalogChanged

  useEffect(() => {mounted.current = true; return () => {mounted.current = false}}, [])
  useEffect(() => {composing.current = false}, [active])
  useEffect(() => {
    if (!active || busy || pending || disabled) return
    return watchAcademyDesigns({
      pending: designs.some(design => design.status === 'generating'),
      publish: rows => {
        setDesigns(rows)
        let changed = false
        for (const design of rows) if (design.status === 'ready' && !announced.current.has(design.id)) {
          announced.current.add(design.id); changed = true
        }
        if (changed) catalogChanged.current()
      },
      failure: failure => setError(failure.copy ?? failure.message),
    })
  }, [active, busy, pending, disabled, refresh])

  const apply = (receipt: AcademyReceipt, identity: AcademyDesignPending) => {
    if (!academyDesignReceiptMatches(receipt, identity)) {setError({key: 'ui.http.invalidResponse'}); return}
    if (receipt.status === 'unknown') return
    try {
      if (readAcademyDesignPending(sessionStorage)?.requestId !== identity.requestId) throw Error('changed academy request')
      sessionStorage.removeItem(academyDesignPendingKey)
      setPending(null)
    } catch {setStorageError(true); return}
    if (receipt.status === 'failed') setError({key: 'ui.academy.error.' + receipt.result.error_code})
    else setError('')
    setRefresh(value => value + 1)
  }
  const recover = async () => {
    if (!pending || lock.current) return
    const identity = pending, operation = epoch.current
    lock.current = true; setBusy(true)
    try {
      const result = await academyApi.receipt(identity.requestId)
      if (!mounted.current || !activeRef.current || operation !== epoch.current) return
      if (result.ok) apply(result.data, identity)
      else setError(result.copy ?? result.message)
    } finally {lock.current = false; if (mounted.current) setBusy(false)}
  }
  useEffect(() => {
    if (!active || busy || disabled || !pending) return
    const key = pending.requestId + ':' + epoch.current
    if (recovered.current === key) return
    recovered.current = key
    void recover()
  }, [active, busy, disabled, pending?.requestId])

  const update = (next: AcademyTopicDraft) => {
    setDraft(next)
    setDraftFailed(!draftStorage.write(next))
  }
  const create = async () => {
    if (disabled || pending || storageError || lock.current || composing.current || !draft.topic.trim()) return
    lock.current = true
    const identity = {requestId: crypto.randomUUID(), ...draft}, operation = epoch.current
    try {
      if (readAcademyDesignPending(sessionStorage)) throw Error('pending academy request')
      sessionStorage.setItem(academyDesignPendingKey, JSON.stringify(identity))
    } catch {lock.current = false; setStorageError(true); return}
    setPending(identity); setBusy(true); setError('')
    try {
      const result = await academyApi.design({request_id: identity.requestId, topic: identity.topic, background: identity.background})
      if (!mounted.current || !activeRef.current || operation !== epoch.current) return
      if (result.ok) apply(result.data, identity)
      else if (isAcademyUnconfirmedWrite(result)) setError(result.copy ?? result.message)
      else {
        if (readAcademyDesignPending(sessionStorage)?.requestId === identity.requestId) {
          sessionStorage.removeItem(academyDesignPendingKey); setPending(null)
        }
        setError(result.copy ?? result.message)
      }
    } catch {if (mounted.current) setStorageError(true)}
    finally {lock.current = false; if (mounted.current) setBusy(false)}
  }

  if (!active) return null
  return <Section title={t('ui.academy.createTopicCourse')} className="mt-4">
    <p className="mb-3 text-sm leading-reading text-muted">{t('ui.academy.authoringIntro')}</p>
    <form className="space-y-3" onSubmit={event => {event.preventDefault(); void create()}}
      onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
      onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}>
      <label className="block space-y-1 text-sm"><span>{t('ui.academy.topic')}</span><input className="ui-field w-full" maxLength={500} value={draft.topic} placeholder={t('ui.academy.topicPlaceholder')} onChange={event => update({...draft, topic: event.target.value})} /></label>
      <details><summary className="cursor-pointer rounded-lg text-xs text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.background')}</summary>
        <label className="mt-2 block"><span className="sr-only">{t('ui.academy.background')}</span><textarea rows={3} maxLength={2000} className="ui-field w-full resize-y leading-reading" value={draft.background} onChange={event => update({...draft, background: event.target.value})} /></label>
      </details>
      <Button type="submit" size="sm" variant="primary" disabled={disabled || busy || !!pending || storageError || !draft.topic.trim()} loading={busy} icon={<Sparkles className="h-3.5 w-3.5" />}>{t('ui.academy.generateCourse')}</Button>
      <p className="text-xs leading-reading text-muted">{t('ui.academy.authoringModelHint')}</p>
    </form>
    {draftFailed ? <p role="status" className="mt-2 text-sm text-warn">{t('ui.academy.authoringDraftError')}</p> : null}
    {storageError ? <p role="alert" className="mt-2 text-sm text-danger">{t('ui.academy.authoringStorageError')}</p> : null}
    {error ? <p role="alert" className="mt-2 text-sm text-danger">{noticeText(error)}</p> : null}
    {pending ? <div role="status" className="mt-3 space-y-2 rounded-xl border border-warn/40 p-3 text-sm">
      <p>{t('ui.academy.authoringPending')}</p><p className="break-all font-mono text-xs">{t('ui.academy.receiptId', {id: pending.requestId})}</p>
      <Button size="xs" disabled={busy} onClick={() => void recover()}>{t('ui.academy.authoringReceipt')}</Button>
    </div> : null}
    <div className="refresh-action-container mt-4 flex items-center justify-between gap-2"><h3 className="ui-card-title">{t('ui.academy.authoringHistory')}</h3><RefreshButton size="xs" variant="ghost" label={t('ui.academy.refreshDesigns')} disabled={busy || !!pending || disabled} onClick={() => setRefresh(value => value + 1)} /></div>
    <ol className="mt-3 max-h-80 space-y-3 overflow-y-auto">{designs.map(design => <li key={design.id} data-design-id={design.id} className="space-y-2 rounded-xl border border-line p-3">
      <h4 className="break-words text-sm font-medium">{design.topic}</h4>
      <p role="status" className="flex items-center gap-2 text-sm text-muted">{design.status === 'generating' ? <Spinner /> : null}{t(design.status === 'generating' ? 'ui.academy.designPhase.' + design.phase : design.status === 'ready' ? 'ui.academy.designReady' : design.status === 'failed' ? 'ui.academy.designFailed' : 'ui.academy.designUnknown')}</p>
      {design.error_code ? <p className="text-sm leading-reading text-warn">{hasTranslation('ui.academy.error.' + design.error_code) ? t('ui.academy.error.' + design.error_code) : design.error_code}</p> : null}
      {design.review_notes.length ? <ul className="list-inside list-disc space-y-1 text-sm leading-reading">{design.review_notes.map((note, index) => <li key={index}>{note}</li>)}</ul> : null}
      {design.status === 'ready' ? <Button size="sm" variant="subtle" disabled={disabled || busy || !!pending} icon={<BookOpen className="h-3.5 w-3.5" />} onClick={() => onOpenCourse(design.course_id)}>{t('ui.academy.startDesignedCourse')}</Button> : null}
    </li>)}</ol>
  </Section>
}
