import {useEffect, useId, useRef, useState} from 'react'
import {t, useLocale} from '@/i18n'
import type {AcademyLesson, AcademyReceipt, AcademyWorldSession, AcademyWorldView, NoticeText} from '@/types'
import {academyApi, academyReceiptMatches, academyWorldParams, isAcademyUnconfirmedWrite} from '@/lib/academy'
import {noticeText} from '@/lib/noticeText'
import {Badge, Button} from '@/components/ui/primitives'

type PendingWorld = {kind: 'start' | 'predict' | 'observe' | 'complete'; requestId: string}

/** 微世界状态可视化；只渲染宿主内核返回的结构化状态。 */
export function AcademyWorldState({kind, state}: {kind: string; state: Record<string, unknown>}) {
  useLocale()
  if (kind === 'fraction-bar') {
    const parts = Number(state.parts), shaded = Number(state.shaded)
    return <div data-world-state="fraction-bar" className="space-y-2">
      <div className="flex flex-wrap gap-1" role="img" aria-label={String(state.label)}>
        {Array.from({length: parts}, (_, index) => <span key={index} className={'h-8 w-8 rounded border border-line ' + (index < shaded ? 'bg-brand' : 'bg-canvas')} />)}
      </div>
      <p className="text-sm font-medium">{String(state.label)}</p>
    </div>
  }
  if (kind === 'group-array') {
    const groups = Number(state.groups), perGroup = Number(state.per_group)
    return <div data-world-state="group-array" className="space-y-2">
      <div className="space-y-1" role="img" aria-label={String(state.product)}>
        {Array.from({length: groups}, (_, row) => <div key={row} className="flex gap-1">{Array.from({length: perGroup}, (_, column) => <span key={column} className="h-4 w-4 rounded-full bg-brand" />)}</div>)}
      </div>
      <p className="text-sm font-medium">{String(state.repeated)} = {String(state.total)}</p>
      <p className="text-xs text-muted">{String(state.product)} = {String(state.total)}</p>
    </div>
  }
  const balanced = Boolean(state.balanced)
  return <div data-world-state="balance-scale" className="space-y-2">
    <div className="flex items-center gap-3 text-sm">
      <span className="rounded-lg border border-line px-3 py-2">{String(state.left)} g</span>
      <Badge tone={balanced ? 'ok' : 'warn'}>{balanced ? '=' : '≠'}</Badge>
      <span className="rounded-lg border border-line px-3 py-2">{String(state.right)} g</span>
    </div>
    <p className="text-xs text-muted">{String(state.difference)}</p>
  </div>
}

/** 微世界卡片：预测、参数操纵、状态观察与解释完成。 */
export function AcademyWorldCard({kind, session, params, note, busy, onParams, onNote, onStart, onPredict, onObserve, onComplete}: {
  kind: string; session: AcademyWorldSession | null; params: Record<string, number>; note: string; busy: boolean
  onParams: (params: Record<string, number>) => void; onNote: (note: string) => void
  onStart: () => void; onPredict: (option: string) => void; onObserve: () => void; onComplete: () => void
}) {
  useLocale()
  const name = useId()
  const [choice, setChoice] = useState('')
  const composing = useRef(false)
  const spec = academyWorldParams[kind] ?? []
  const last = session?.observations.at(-1)
  const valid = spec.every(item => Number.isInteger(params[item.key]) && params[item.key] >= item.min && params[item.key] <= item.max)
  if (!session) return <div data-academy-world="offer" className="space-y-2 rounded-xl border border-brand/40 p-3">
    <p className="text-sm font-medium">{t('ui.academy.world.title')}</p>
    <p className="text-xs leading-reading text-muted">{t('ui.academy.world.intro')}</p>
    <Button size="sm" disabled={busy} onClick={onStart}>{t('ui.academy.world.start')}</Button>
  </div>
  return <div data-academy-world={session.status} className="space-y-3 rounded-xl border border-brand/40 p-3">
    <p className="text-sm font-medium">{t('ui.academy.world.title')}</p>
    <p className="whitespace-pre-wrap text-base leading-reading">{session.challenge.prompt}</p>
    {session.status === 'predicting' ? <form className="space-y-2" onSubmit={event => {event.preventDefault(); if (choice) onPredict(choice)}}>
      <fieldset className="space-y-2"><legend className="text-sm">{t('ui.academy.world.predictLabel')}</legend>
        {session.challenge.options.map(option => <label key={option.id} className="flex cursor-pointer items-start gap-2 rounded-lg border border-line p-3 text-sm focus-within:border-brand">
          <input type="radio" name={name} value={option.id} checked={choice === option.id} className="mt-1 accent-brand" onChange={() => setChoice(option.id)} />
          <span className="min-w-0 break-words">{option.label}</span>
        </label>)}
      </fieldset>
      <Button type="submit" size="sm" disabled={busy || !choice}>{t('ui.academy.world.predict')}</Button>
    </form> : <>
      {session.prediction ? <p className={'text-sm font-medium ' + (session.prediction.correct ? 'text-ok' : 'text-warn')}>
        {t(session.prediction.correct ? 'ui.academy.world.predictedCorrect' : 'ui.academy.world.predictedWrong')}</p> : null}
      <div className="space-y-2">
        <p className="text-xs font-medium text-muted">{t('ui.academy.world.paramsLabel')}</p>
        <div className="flex flex-wrap gap-2">{spec.map(item => <label key={item.key} className="flex items-center gap-1 text-sm">
          <span className="font-mono text-xs">{item.key}</span>
          <input type="number" className="ui-field w-20" min={item.min} max={item.max} step={1} value={Number.isInteger(params[item.key]) ? params[item.key] : ''}
            disabled={busy || session.status === 'completed'} onChange={event => onParams({...params, [item.key]: Math.trunc(Number(event.target.value))})} />
        </label>)}</div>
        <Button size="sm" disabled={busy || !valid || session.status === 'completed'} onClick={onObserve}>{t('ui.academy.world.observe')}</Button>
      </div>
      {last ? <div className="space-y-1 rounded-lg bg-canvas p-3">
        <p className="text-xs font-medium text-muted">{t('ui.academy.world.stateLabel')}</p>
        <AcademyWorldState kind={kind} state={last.state} />
      </div> : null}
      <p className="text-xs text-muted">{t('ui.academy.world.observations', {count: session.observations.length})}</p>
      {session.status === 'completed' ? <div role="status" className="space-y-1 rounded-lg bg-canvas p-3 text-sm">
        <p className="font-medium text-ok">{t('ui.academy.world.completed')}</p>
        <p className="whitespace-pre-wrap leading-reading">{session.note}</p>
      </div> : <form className="space-y-2" onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
        onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}
        onSubmit={event => {event.preventDefault(); if (!composing.current && note.trim()) onComplete()}}>
        <label className="block space-y-1 text-sm"><span>{t('ui.academy.world.noteLabel')}</span>
          <textarea className="ui-field min-h-20 w-full" value={note} placeholder={t('ui.academy.world.notePlaceholder')} onChange={event => onNote(event.target.value)} /></label>
        <Button type="submit" size="sm" disabled={busy || !note.trim()}>{t('ui.academy.world.complete')}</Button>
      </form>}
    </>}
  </div>
}

/** 微世界容器：读取会话、持久草稿、未知回执只回查。 */
export function AcademyWorld({projectId, lesson, active, disabled, onCompleted}: {projectId: string; lesson: AcademyLesson; active: boolean; disabled: boolean; onCompleted?: () => void}) {
  useLocale()
  const task = lesson.world
  const noteKey = 'academy.world.note.' + JSON.stringify([projectId, lesson.id])
  const pendingKey = 'academy.world.pending.' + projectId
  const [view, setView] = useState<AcademyWorldView | null>(null)
  const [params, setParams] = useState<Record<string, number>>({})
  const [note, setNote] = useState(() => {try {return sessionStorage.getItem(noteKey) ?? ''} catch {return ''}})
  const [revision, setRevision] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<NoticeText | ''>('')
  const [pending, setPending] = useState<PendingWorld | null>(null)
  const session = view?.world ?? null

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(pendingKey)
      if (!raw) return
      const value = JSON.parse(raw) as {kind?: unknown; requestId?: unknown; projectId?: unknown}
      if (value && ['start', 'predict', 'observe', 'complete'].includes(String(value.kind))
          && typeof value.requestId === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value.requestId) && value.projectId === projectId) {
        setPending({kind: value.kind as PendingWorld['kind'], requestId: value.requestId})
      } else sessionStorage.removeItem(pendingKey)
    } catch {
      try {sessionStorage.removeItem(pendingKey)} catch {setPending(null)}
    }
  }, [pendingKey, projectId])

  useEffect(() => {
    try {sessionStorage.setItem(noteKey, note)} catch {setError({key: 'ui.academy.error.store-invalid'})}
  }, [note, noteKey])

  useEffect(() => {
    if (!active || !task) return
    const controller = new AbortController()
    void (async () => {
      const result = await academyApi.world(projectId, lesson.id, controller.signal)
      if (controller.signal.aborted) return
      if (result.ok) {
        setView(result.data)
        const current = result.data.world
        if (current) setParams({...current.params} as Record<string, number>)
        else setParams(Object.fromEntries((academyWorldParams[task.kind] ?? []).map(item => [item.key, item.min])))
      } else setError(result.copy ?? result.message)
    })()
    return () => controller.abort()
  }, [active, task, projectId, lesson.id, revision])

  useEffect(() => {if (session?.status === 'completed') onCompleted?.()}, [session?.status, onCompleted])

  if (!task) return null
  const clearPending = () => {
    try {sessionStorage.removeItem(pendingKey)} catch {setPending(null)}
    setPending(null)
  }
  const applyReceipt = (receipt: AcademyReceipt, identity: PendingWorld) => {
    if (receipt.status === 'unknown') return
    if (!academyReceiptMatches(receipt, identity.requestId, projectId) || !receipt.result.world) {setError({key: 'ui.http.invalidResponse'}); return}
    if (receipt.status === 'failed') {
      clearPending()
      setError({key: receipt.result.error_code ? 'ui.academy.error.' + receipt.result.error_code : 'ui.academy.error.invalid-request'})
      return
    }
    clearPending()
    setRevision(value => value + 1)
  }
  const write = async (identity: PendingWorld, call: () => ReturnType<typeof academyApi.worldStart>) => {
    if (disabled || busy || pending) return
    try {sessionStorage.setItem(pendingKey, JSON.stringify({...identity, projectId}))} catch {setError({key: 'ui.academy.error.store-invalid'})}
    setPending(identity); setBusy(true); setError('')
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
  const locked = busy || disabled || pending !== null

  return <div className="space-y-2">
    {error ? <p role="alert" className="text-sm text-danger">{noticeText(error)}</p> : null}
    {pending ? <div role="status" className="space-y-2 rounded-xl border border-warn/40 p-3 text-sm">
      <p>{t('ui.academy.world.pending')}</p>
      <p className="break-all font-mono text-xs">{t('ui.academy.receiptId', {id: pending.requestId})}</p>
      <Button size="xs" disabled={busy} onClick={() => void recheck()}>{t('ui.academy.recheckReceipt')}</Button>
    </div> : null}
    <AcademyWorldCard key={(session?.id ?? 'offer') + ':' + (session?.status ?? '')} kind={task.kind} session={session} params={params} note={note} busy={locked}
      onParams={setParams} onNote={setNote}
      onStart={() => {const requestId = crypto.randomUUID(); void write({kind: 'start', requestId}, () => academyApi.worldStart(projectId, {request_id: requestId, lesson_id: lesson.id}))}}
      onPredict={option => {const requestId = crypto.randomUUID(); void write({kind: 'predict', requestId}, () => academyApi.worldPredict(projectId, {request_id: requestId, option_id: option}))}}
      onObserve={() => {const requestId = crypto.randomUUID(); void write({kind: 'observe', requestId}, () => academyApi.worldObserve(projectId, {request_id: requestId, params}))}}
      onComplete={() => {const requestId = crypto.randomUUID(); void write({kind: 'complete', requestId}, () => academyApi.worldComplete(projectId, {request_id: requestId, note: note.trim()}))}} />
  </div>
}
