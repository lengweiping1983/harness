import {useEffect, useRef, useState} from 'react'
import {t, useLocale} from '@/i18n'
import type {AcademyLesson, AcademyReceipt, AcademyStruggleSession, AcademyStruggleView, NoticeText} from '@/types'
import {academyApi, academyReceiptMatches, isAcademyUnconfirmedWrite} from '@/lib/academy'
import {noticeText} from '@/lib/noticeText'
import {Button} from '@/components/ui/primitives'

type PendingStruggle = {kind: 'start' | 'attempt' | 'reveal'; requestId: string}

/** 挣扎卡片：挑战、尝试输入、窗口倒计时与揭示后的对照提示。 */
export function AcademyStruggleCard({task, session, remaining, draft, busy, onDraft, onStart, onAttempt, onReveal}: {
  task: NonNullable<AcademyLesson['struggle']>; session: AcademyStruggleSession | null; remaining: number
  draft: string; busy: boolean; onDraft: (text: string) => void; onStart: () => void; onAttempt: () => void; onReveal: () => void
}) {
  useLocale()
  const composing = useRef(false)
  if (session?.status === 'revealed') return <div data-academy-struggle="revealed" className="space-y-2 rounded-xl border border-ok/40 p-3">
    <p className="text-sm font-medium text-ok">{t('ui.academy.struggle.revealedTitle')}</p>
    <p className="text-xs leading-reading text-muted">{t('ui.academy.struggle.revealedHint')}</p>
    {session.attempt ? <div className="rounded-lg bg-canvas p-3 text-sm"><p className="text-xs font-medium text-muted">{t('ui.academy.struggle.frozenAttempt')}</p><p className="mt-1 whitespace-pre-wrap leading-reading">{session.attempt}</p></div> : null}
  </div>
  return <div data-academy-struggle={session ? session.status : 'offer'} className="space-y-3 rounded-xl border border-warn/40 p-3">
    <div><p className="text-sm font-medium">{t('ui.academy.struggle.title')}</p>
      <p className="mt-1 text-xs font-medium text-muted">{t('ui.academy.struggle.challenge')}</p>
      <p className="mt-1 whitespace-pre-wrap text-base leading-reading">{task.challenge}</p></div>
    {!session ? <>
      <p className="text-xs leading-reading text-muted">{t('ui.academy.struggle.windowHint', {minutes: task.window_minutes})}</p>
      <Button size="sm" disabled={busy} onClick={onStart}>{t('ui.academy.struggle.start')}</Button>
    </> : <>
      <p className="text-xs text-warn">{t('ui.academy.struggle.countdown', {seconds: remaining})}</p>
      <form className="space-y-2" onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
        onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}
        onSubmit={event => {event.preventDefault(); if (!composing.current) onAttempt()}}>
        <label className="block space-y-1 text-sm"><span>{t('ui.academy.struggle.attemptLabel')}</span>
          <textarea className="ui-field min-h-24 w-full" value={draft} placeholder={t('ui.academy.struggle.attemptPlaceholder')}
            onChange={event => onDraft(event.target.value)} /></label>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" size="sm" disabled={busy || !draft.trim()}>{t('ui.academy.struggle.saveAttempt')}</Button>
          <Button type="button" size="sm" variant="secondary" disabled={busy || (session.status !== 'attempted' && remaining > 0)} onClick={onReveal}>{t('ui.academy.struggle.reveal')}</Button>
        </div>
      </form>
    </>}
  </div>
}

/** 挣扎优先容器：读取会话、本地倒计时、未知回执只回查；向面板报告讲解门控。 */
export function AcademyStruggle({projectId, lesson, active, disabled, onPhase}: {
  projectId: string; lesson: AcademyLesson; active: boolean; disabled: boolean; onPhase: (revealed: boolean) => void
}) {
  useLocale()
  const task = lesson.struggle
  const draftKey = 'academy.struggle.draft.' + JSON.stringify([projectId, lesson.id])
  const pendingKey = 'academy.struggle.pending.' + projectId
  const [view, setView] = useState<AcademyStruggleView | null>(null)
  const [loadedAt, setLoadedAt] = useState(0)
  const [tick, setTick] = useState(0)
  const [revision, setRevision] = useState(0)
  const [draft, setDraft] = useState(() => {try {return sessionStorage.getItem(draftKey) ?? ''} catch {return ''}})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<NoticeText | ''>('')
  const [pending, setPending] = useState<PendingStruggle | null>(null)
  const phaseRef = useRef(onPhase)
  phaseRef.current = onPhase
  const session = view?.struggle ?? null
  const status = session?.status

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(pendingKey)
      if (!raw) return
      const value = JSON.parse(raw) as {kind?: unknown; requestId?: unknown; projectId?: unknown}
      if (value && (value.kind === 'start' || value.kind === 'attempt' || value.kind === 'reveal')
          && typeof value.requestId === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value.requestId) && value.projectId === projectId) {
        setPending({kind: value.kind, requestId: value.requestId})
      } else sessionStorage.removeItem(pendingKey)
    } catch {
      try {sessionStorage.removeItem(pendingKey)} catch {setPending(null)}
    }
  }, [pendingKey, projectId])

  useEffect(() => {
    try {sessionStorage.setItem(draftKey, draft)} catch {setError({key: 'ui.academy.error.store-invalid'})}
  }, [draft, draftKey])

  useEffect(() => {
    if (!active || !task) return
    const controller = new AbortController()
    void (async () => {
      const result = await academyApi.struggle(projectId, lesson.id, controller.signal)
      if (controller.signal.aborted) return
      if (result.ok) {setView(result.data); setLoadedAt(Date.now())}
      else setError(result.copy ?? result.message)
    })()
    return () => controller.abort()
  }, [active, task, projectId, lesson.id, revision])

  useEffect(() => {
    phaseRef.current(status === 'revealed')
  }, [status, lesson.id])

  useEffect(() => {
    if (!active || !session || session.status === 'revealed') return
    const timer = setInterval(() => setTick(value => value + 1), 1000)
    return () => clearInterval(timer)
  }, [active, session])

  if (!task) return null
  const base = view?.remaining_seconds ?? task.window_minutes * 60
  const remaining = session && session.status !== 'revealed' ? Math.max(0, base - Math.floor((Date.now() - loadedAt) / 1000)) : 0
  void tick

  const clearPending = () => {
    try {sessionStorage.removeItem(pendingKey)} catch {setPending(null)}
    setPending(null)
  }
  const applyReceipt = (receipt: AcademyReceipt, identity: PendingStruggle) => {
    if (!academyReceiptMatches(receipt, identity.requestId, projectId) || !receipt.result.struggle) {
      if (receipt.status === 'unknown') return
      setError({key: 'ui.http.invalidResponse'})
      return
    }
    if (receipt.status === 'unknown') return
    if (receipt.status === 'failed') {
      clearPending()
      setError({key: receipt.result.error_code ? 'ui.academy.error.' + receipt.result.error_code : 'ui.academy.error.invalid-request'})
      return
    }
    clearPending()
    setRevision(value => value + 1)
  }
  const remember = (identity: PendingStruggle) => {
    try {sessionStorage.setItem(pendingKey, JSON.stringify({...identity, projectId}))} catch {setError({key: 'ui.academy.error.store-invalid'})}
    setPending(identity)
  }
  const write = async (identity: PendingStruggle, call: () => ReturnType<typeof academyApi.struggleStart>) => {
    if (disabled || busy || pending) return
    remember(identity); setBusy(true); setError('')
    const outcome = await call()
    if (outcome.ok) applyReceipt(outcome.data, identity)
    else if (isAcademyUnconfirmedWrite(outcome)) setError(outcome.copy ?? outcome.message)
    else {clearPending(); setError(outcome.copy ?? outcome.message)}
    setBusy(false)
  }
  const recheck = async () => {
    if (!pending || busy) return
    setBusy(true)
    const outcome = await academyApi.receipt(pending.requestId)
    if (outcome.ok) applyReceipt(outcome.data, pending)
    else setError(outcome.copy ?? outcome.message)
    setBusy(false)
  }

  return <div className="space-y-2">
    {error ? <p role="alert" className="text-sm text-danger">{noticeText(error)}</p> : null}
    {pending ? <div role="status" className="space-y-2 rounded-xl border border-warn/40 p-3 text-sm">
      <p>{t('ui.academy.struggle.pending')}</p>
      <p className="break-all font-mono text-xs">{t('ui.academy.receiptId', {id: pending.requestId})}</p>
      <Button size="xs" disabled={busy} onClick={() => void recheck()}>{t('ui.academy.recheckReceipt')}</Button>
    </div> : null}
    <AcademyStruggleCard task={task} session={session} remaining={remaining} draft={draft} busy={busy || disabled || pending !== null}
      onDraft={setDraft}
      onStart={() => {const requestId = crypto.randomUUID(); void write({kind: 'start', requestId}, () => academyApi.struggleStart(projectId, {request_id: requestId, lesson_id: lesson.id}))}}
      onAttempt={() => {const requestId = crypto.randomUUID(); void write({kind: 'attempt', requestId}, () => academyApi.struggleAttempt(projectId, {request_id: requestId, text: draft.trim()}))}}
      onReveal={() => {const requestId = crypto.randomUUID(); void write({kind: 'reveal', requestId}, () => academyApi.struggleReveal(projectId, {request_id: requestId}))}} />
  </div>
}
