import {useEffect, useId, useRef, useState} from 'react'
import {ArrowDown, ArrowUp, Check, Send} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import type {AcademyLesson, AcademyReceipt, AcademyStepPublic, AcademyStepsView, NoticeText} from '@/types'
import {academyApi, academyReceiptMatches, isAcademyUnconfirmedWrite} from '@/lib/academy'
import {noticeText} from '@/lib/noticeText'
import {Button} from '@/components/ui/primitives'

type Feedback = {correct: boolean; text: string; scaffold?: string; numberLine?: {min: number; max: number; value: number}}

/** 数轴可视化：把数值答案落到数轴上（表象支持），不揭示正确答案。 */
export function AcademyNumberLine({min, max, value}: {min: number; max: number; value: number}) {
  useLocale()
  const span = max - min || 1
  const pct = Math.max(0, Math.min(100, ((value - min) / span) * 100))
  return <div data-academy-number-line className="space-y-1 rounded-lg bg-canvas p-3">
    <div className="relative h-6" role="img" aria-label={t('ui.academy.steps.numberLineLabel', {value})}>
      <div className="absolute left-0 right-0 top-1/2 h-0.5 -translate-y-1/2 bg-line" />
      <div className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand" style={{left: pct + '%'}} />
    </div>
    <div className="flex justify-between text-xs text-muted"><span>{min}</span><span className="font-medium text-brand">{value}</span><span>{max}</span></div>
  </div>
}

/** 单步卡片：按类型渲染输入，提交后即时反馈；答对显示下一步。 */
export function AcademyStepCard({step, busy, feedback, cheer, onSubmit, onNext}: {
  step: AcademyStepPublic; busy: boolean; feedback: Feedback | null; cheer?: string; onSubmit: (response: string | number | string[] | Record<string, string>) => void; onNext: () => void
}) {
  useLocale()
  const name = useId()
  const composing = useRef(false)
  const [text, setText] = useState('')
  const [choice, setChoice] = useState('')
  const [order, setOrder] = useState<string[]>(() => (step.items ?? []).map(item => item.id))
  const [pairs, setPairs] = useState<Record<string, string>>({})
  const locked = busy || feedback?.correct === true
  const move = (index: number, delta: number) => {
    const target = index + delta
    if (target < 0 || target >= order.length) return
    const next = [...order]
    ;[next[index], next[target]] = [next[target], next[index]]
    setOrder(next)
  }
  const submit = () => {
    if (composing.current || locked) return
    if (step.kind === 'explain' || step.kind === 'numeric') {
      if (!text.trim()) return
      onSubmit(step.kind === 'numeric' ? Number(text) : text.trim())
    } else if (step.kind === 'choice') {
      if (choice) onSubmit(choice)
    } else if (step.kind === 'ordering') {
      onSubmit(order)
    } else if (step.kind === 'matching') {
      onSubmit(pairs)
    }
  }
  const ready = step.kind === 'ordering' ? order.length > 0 : step.kind === 'choice' ? Boolean(choice) : step.kind === 'matching' ? (step.left ?? []).every(item => Boolean(pairs[item.id])) : Boolean(text.trim())
  return <div data-academy-step={step.id} data-step-kind={step.kind} className="space-y-3 rounded-xl border border-brand/40 p-3">
    {step.kind === 'explain' && step.content ? <div className="rounded-lg bg-canvas p-3 text-base leading-reading">{step.content}</div> : null}
    <p className="whitespace-pre-wrap text-base leading-reading">{step.prompt}</p>
    {feedback ? <div role="status" className="space-y-1">
      {feedback.correct && cheer ? <p className="text-sm font-medium text-ok">{cheer}</p> : null}
      <p className={'flex items-center gap-1 text-sm font-medium ' + (feedback.correct ? 'text-ok' : 'text-warn')}>
        {feedback.correct ? <Check className="h-4 w-4" /> : null}{feedback.text}</p>
    </div> : null}
    {feedback && !feedback.correct && feedback.scaffold ? <div className="rounded-lg border border-warn/40 bg-canvas p-3 text-sm leading-reading"><p className="text-xs font-medium text-warn">{t('ui.academy.steps.scaffoldLabel')}</p><p className="mt-1 whitespace-pre-wrap">{feedback.scaffold}</p></div> : null}
    {feedback?.numberLine ? <AcademyNumberLine min={feedback.numberLine.min} max={feedback.numberLine.max} value={feedback.numberLine.value} /> : null}
    {feedback?.correct ? <Button size="sm" onClick={onNext}>{t('ui.academy.steps.next')}</Button> : <form className="space-y-2"
      onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
      onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}
      onSubmit={event => {event.preventDefault(); submit()}}>
      {step.kind === 'explain' ? <textarea className="ui-field min-h-20 w-full" value={text} disabled={locked} placeholder={t('ui.academy.steps.explainPlaceholder')} aria-label={t('ui.academy.steps.explainPrompt')} onChange={event => setText(event.target.value)} /> : null}
      {step.kind === 'numeric' ? <input type="number" className="ui-field w-32" value={text} disabled={locked} placeholder={t('ui.academy.steps.numericPlaceholder')} onChange={event => setText(event.target.value)} /> : null}
      {step.kind === 'choice' ? <fieldset className="space-y-2" disabled={locked}>{(step.options ?? []).map(option => <label key={option.id} className="flex cursor-pointer items-start gap-2 rounded-lg border border-line p-3 text-sm focus-within:border-brand">
        <input type="radio" name={name} value={option.id} checked={choice === option.id} className="mt-1 accent-brand" onChange={() => setChoice(option.id)} />
        <span className="min-w-0 break-words">{option.label}</span></label>)}</fieldset> : null}
      {step.kind === 'ordering' ? <ol className="space-y-2">{order.map((id, index) => {const item = (step.items ?? []).find(entry => entry.id === id); return <li key={id} className="flex items-center gap-2 rounded-lg border border-line p-2 text-sm">
        <span className="min-w-0 flex-1 break-words">{item?.label}</span>
        <Button type="button" size="xs" variant="ghost" disabled={locked || index === 0} aria-label={t('ui.academy.steps.moveUp')} onClick={() => move(index, -1)} icon={<ArrowUp className="h-3.5 w-3.5" />} />
        <Button type="button" size="xs" variant="ghost" disabled={locked || index === order.length - 1} aria-label={t('ui.academy.steps.moveDown')} onClick={() => move(index, 1)} icon={<ArrowDown className="h-3.5 w-3.5" />} />
      </li>})}</ol> : null}
      {step.kind === 'matching' ? <div className="space-y-2">{(step.left ?? []).map(item => <label key={item.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-line p-2 text-sm">
        <span className="min-w-0 flex-1 break-words">{item.label}</span>
        <select className="ui-field" disabled={locked} value={pairs[item.id] ?? ''} aria-label={item.label} onChange={event => setPairs(previous => ({...previous, [item.id]: event.target.value}))}>
          <option value="" disabled>—</option>
          {(step.right ?? []).map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
      </label>)}</div> : null}
      <Button type="submit" size="sm" disabled={locked || !ready} icon={<Send className="h-3.5 w-3.5" />}>{t('ui.academy.steps.submit')}</Button>
    </form>}
  </div>
}

/** 步骤播放器容器：一次一步、即时反馈、进度指示；未知回执只回查。 */
export function AcademySteps({projectId, lesson, active, disabled}: {projectId: string; lesson: AcademyLesson; active: boolean; disabled: boolean}) {
  useLocale()
  const steps = lesson.steps
  const pendingKey = 'academy.steps.pending.' + projectId
  const [view, setView] = useState<AcademyStepsView | null>(null)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [revision, setRevision] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<NoticeText | ''>('')
  const [pending, setPending] = useState<{requestId: string} | null>(null)

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(pendingKey)
      if (!raw) return
      const value = JSON.parse(raw) as {requestId?: unknown; projectId?: unknown}
      if (value && typeof value.requestId === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value.requestId) && value.projectId === projectId) setPending({requestId: value.requestId})
      else sessionStorage.removeItem(pendingKey)
    } catch {
      try {sessionStorage.removeItem(pendingKey)} catch {setPending(null)}
    }
  }, [pendingKey, projectId])

  useEffect(() => {
    if (!active || !steps?.length) return
    const controller = new AbortController()
    void (async () => {
      const result = await academyApi.steps(projectId, lesson.id, controller.signal)
      if (controller.signal.aborted) return
      if (result.ok) setView(result.data)
      else setError(result.copy ?? result.message)
    })()
    return () => controller.abort()
  }, [active, steps, projectId, lesson.id, revision])

  if (!steps?.length) return null
  const done = new Set((view?.responses ?? []).filter(response => response.correct).map(response => response.step_id))
  const total = steps.length
  const currentIndex = steps.findIndex(step => !done.has(step.id))
  const completed = view?.completed || currentIndex === -1
  const current = currentIndex >= 0 ? steps[currentIndex] : null
  const correctCount = done.size
  const cheer = t('ui.academy.steps.cheer.' + (correctCount % 3))

  const clearPending = () => {
    try {sessionStorage.removeItem(pendingKey)} catch {setPending(null)}
    setPending(null)
  }
  const applyReceipt = (receipt: AcademyReceipt, requestId: string) => {
    if (receipt.status === 'unknown') return
    if (!academyReceiptMatches(receipt, requestId, projectId) || !receipt.result.step) {setError({key: 'ui.http.invalidResponse'}); return}
    if (receipt.status === 'failed') {
      clearPending()
      setError({key: receipt.result.error_code ? 'ui.academy.error.' + receipt.result.error_code : 'ui.academy.error.invalid-request'})
      return
    }
    clearPending()
    const result = receipt.result.step
    setFeedback({correct: result.correct, text: result.feedback ?? t(result.correct ? 'ui.academy.steps.correct' : 'ui.academy.steps.tryAgain'), scaffold: result.scaffold ?? undefined, numberLine: result.number_line ?? undefined})
    if (result.completed) setRevision(value => value + 1)
  }
  const submit = async (response: string | number | string[] | Record<string, string>) => {
    if (disabled || busy || pending || !current) return
    const requestId = crypto.randomUUID()
    try {sessionStorage.setItem(pendingKey, JSON.stringify({requestId, projectId}))} catch {setError({key: 'ui.academy.error.store-invalid'})}
    setPending({requestId}); setBusy(true); setError(''); setFeedback(null)
    const outcome = await academyApi.stepAnswer(projectId, {request_id: requestId, lesson_id: lesson.id, step_id: current.id, response})
    if (outcome.ok) applyReceipt(outcome.data, requestId)
    else if (isAcademyUnconfirmedWrite(outcome)) setError(outcome.copy ?? outcome.message)
    else {clearPending(); setError(outcome.copy ?? outcome.message)}
    setBusy(false)
  }
  const next = () => {setFeedback(null); setRevision(value => value + 1)}
  const recheck = async () => {
    if (!pending || busy) return
    setBusy(true)
    const outcome = await academyApi.receipt(pending.requestId)
    if (outcome.ok) applyReceipt(outcome.data, pending.requestId)
    else setError(outcome.copy ?? outcome.message)
    setBusy(false)
  }

  return <div data-academy-section="steps" className="space-y-2">
    <div className="flex items-center justify-between gap-2">
      <p className="text-sm font-medium">{t('ui.academy.steps.title')}</p>
      <p className="text-xs text-muted">{completed ? t('ui.academy.steps.completed') : t('ui.academy.steps.progress', {current: Math.min(currentIndex + 1, total), total})}</p>
    </div>
    <div className="flex gap-1" aria-hidden>{steps.map((step, index) => <span key={step.id} className={'h-1.5 flex-1 rounded-full ' + (done.has(step.id) ? 'bg-ok' : index === currentIndex ? 'bg-brand' : 'bg-line')} />)}</div>
    {correctCount >= 2 && !completed ? <p className="text-xs font-medium text-ok">{t('ui.academy.steps.streak', {count: correctCount})}</p> : null}
    {error ? <p role="alert" className="text-sm text-danger">{noticeText(error)}</p> : null}
    {pending ? <div role="status" className="space-y-2 rounded-xl border border-warn/40 p-3 text-sm">
      <p>{t('ui.academy.steps.pending')}</p>
      <p className="break-all font-mono text-xs">{t('ui.academy.receiptId', {id: pending.requestId})}</p>
      <Button size="xs" disabled={busy} onClick={() => void recheck()}>{t('ui.academy.recheckReceipt')}</Button>
    </div> : null}
    {completed ? <p role="status" className="rounded-lg bg-canvas p-3 text-sm font-medium text-ok">{t('ui.academy.steps.completed')}</p>
      : current ? <AcademyStepCard key={current.id + ':' + revision} step={current} busy={busy || disabled || pending !== null} feedback={feedback} cheer={cheer} onSubmit={response => void submit(response)} onNext={next} /> : null}
  </div>
}
