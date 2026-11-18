import {useEffect, useId, useRef, useState} from 'react'
import {ArrowDown, ArrowUp, Check, GripVertical, Send} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import type {AcademyLesson, AcademyReceipt, AcademyStepPublic, AcademyStepsView, NoticeText} from '@/types'
import {academyApi, academyNextStep, academyReceiptMatches, isAcademyUnconfirmedWrite} from '@/lib/academy'
import {noticeText} from '@/lib/noticeText'
import {Button} from '@/components/ui/primitives'
import {AcademyVoiceButton} from './AcademyVoice'

type Feedback = {correct: boolean; text: string; scaffold?: string; numberLine?: {min: number; max: number; value: number}; calibration?: string}

/** 作答前的元认知置信度档位。 */
export type AcademyConfidence = 'guess' | 'unsure' | 'sure'

/** 数轴可视化：把数值答案落到数轴上（表象支持），不揭示正确答案。 */
export function AcademyNumberLine({min, max, value, onChange}: {min: number; max: number; value: number; onChange?: (v: number) => void}) {
  useLocale()
  const track = useRef<HTMLDivElement | null>(null)
  const span = max - min || 1
  const pct = Math.max(0, Math.min(100, ((value - min) / span) * 100))
  /** 拖拽数轴点直接作答（可探索输入）；指针位置映射到区间整数。 */
  const pointTo = (clientX: number) => {
    if (!onChange || !track.current) return
    const rect = track.current.getBoundingClientRect()
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / (rect.width || 1)))
    onChange(Math.round(min + ratio * span))
  }
  return <div data-academy-number-line className="space-y-1 rounded-lg bg-canvas p-3">
    <div ref={track} className={'relative h-6 ' + (onChange ? 'cursor-pointer touch-none' : '')}
      role={onChange ? 'slider' : 'img'} aria-label={t('ui.academy.steps.numberLineLabel', {value})}
      aria-valuenow={onChange ? value : undefined} aria-valuemin={onChange ? min : undefined} aria-valuemax={onChange ? max : undefined}
      onPointerDown={onChange ? event => {event.preventDefault(); pointTo(event.clientX)} : undefined}
      onPointerMove={onChange ? event => {if (event.buttons === 1) pointTo(event.clientX)} : undefined}>
      <div className="absolute left-0 right-0 top-1/2 h-0.5 -translate-y-1/2 bg-line" />
      <div className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand" style={{left: pct + '%'}} />
    </div>
    <div className="flex justify-between text-xs text-muted"><span>{min}</span><span className="font-medium text-brand">{value}</span><span>{max}</span></div>
  </div>
}

/** 单步卡片：按类型渲染输入，提交后即时反馈；答对显示下一步。 */
export function AcademyStepCard({step, busy, feedback, cheer, blindspot, onSubmit, onNext}: {
  step: AcademyStepPublic; busy: boolean; feedback: Feedback | null; cheer?: string; blindspot?: boolean; onSubmit: (response: string | number | string[] | Record<string, string>, confidence: AcademyConfidence, diagnosis: string) => void; onNext: () => void
}) {
  useLocale()
  const name = useId()
  const composing = useRef(false)
  const [text, setText] = useState('')
  const [choice, setChoice] = useState('')
  const [order, setOrder] = useState<string[]>(() => (step.items ?? []).map(item => item.id))
  const [pairs, setPairs] = useState<Record<string, string>>({})
  const [confidence, setConfidence] = useState<AcademyConfidence | null>(null)
  const [diagnosis, setDiagnosis] = useState('')
  const dragIndex = useRef<number | null>(null)
  const [selectedLeft, setSelectedLeft] = useState<string | null>(null)
  const locked = busy || feedback?.correct === true
  /** 拖拽排序：直接操纵卡片顺序；键盘上下按钮保留为无障碍等价路径。 */
  const dropOn = (target: number) => {
    const source = dragIndex.current
    dragIndex.current = null
    if (source === null || source === target) return
    const next = [...order]
    const [moved] = next.splice(source, 1)
    next.splice(target, 0, moved)
    setOrder(next)
  }
  const assignRight = (rightId: string) => {
    const slot = selectedLeft ?? (step.left ?? []).find(item => !pairs[item.id])?.id ?? (step.left ?? [])[0]?.id
    if (!slot) return
    setPairs(previous => ({...previous, [slot]: rightId}))
    setSelectedLeft(null)
  }
  const move = (index: number, delta: number) => {
    const target = index + delta
    if (target < 0 || target >= order.length) return
    const next = [...order]
    ;[next[index], next[target]] = [next[target], next[index]]
    setOrder(next)
  }
  const submit = () => {
    if (composing.current || locked || !confidence) return
    if (step.kind === 'explain' || step.kind === 'numeric') {
      if (!text.trim()) return
      onSubmit(step.kind === 'numeric' ? Number(text) : text.trim(), confidence, diagnosis)
    } else if (step.kind === 'choice') {
      if (choice) onSubmit(choice, confidence, diagnosis)
    } else if (step.kind === 'ordering') {
      onSubmit(order, confidence, diagnosis)
    } else if (step.kind === 'matching') {
      onSubmit(pairs, confidence, diagnosis)
    }
  }
  const ready = confidence !== null && (step.kind === 'ordering' ? order.length > 0 : step.kind === 'choice' ? Boolean(choice) : step.kind === 'matching' ? (step.left ?? []).every(item => Boolean(pairs[item.id])) : Boolean(text.trim()))
  return <div data-academy-step={step.id} data-step-kind={step.kind} className="space-y-3 rounded-xl border border-brand/40 p-3">
    {step.kind === 'explain' && step.content ? <div className="rounded-lg bg-canvas p-3 text-base leading-reading">{step.content}</div> : null}
    {step.transfer ? <p className="inline-flex rounded-full border border-brand/40 bg-brand/5 px-2 py-0.5 text-xs font-medium text-brand" title={t('ui.academy.steps.transferHint')}>{t('ui.academy.steps.transferLabel')}</p> : null}
    {blindspot && feedback?.correct !== true ? <p className="inline-flex rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 text-xs font-medium text-warn">{t('ui.academy.steps.blindspotChip')}</p> : null}
    <p className="whitespace-pre-wrap text-base leading-reading">{step.prompt}</p>
    {feedback ? <div role="status" className="space-y-1">
      {feedback.correct && cheer ? <p className="text-sm font-medium text-ok">{cheer}</p> : null}
      <p className={'flex items-center gap-1 text-sm font-medium ' + (feedback.correct ? 'text-ok' : 'text-warn')}>
        {feedback.correct ? <Check className="h-4 w-4" /> : null}{feedback.text}</p>
      {feedback.calibration ? <p className={'text-sm font-medium ' + (feedback.calibration === 'calibrated' ? 'text-ok' : feedback.calibration === 'blindspot' ? 'text-warn' : 'text-muted')}>{t('ui.academy.steps.calibration.' + feedback.calibration)}</p> : null}
    </div> : null}
    {feedback?.correct && step.reference ? <details className="rounded-lg border border-line p-3 text-sm">
      <summary className="cursor-pointer rounded-lg text-xs font-medium text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.steps.compareReference')}</summary>
      <p className="mt-2 whitespace-pre-wrap leading-reading">{step.reference}</p>
      <p className="mt-1 text-xs text-muted">{t('ui.academy.steps.compareHint')}</p>
    </details> : null}
    {feedback && !feedback.correct && feedback.scaffold ? <div className="rounded-lg border border-warn/40 bg-canvas p-3 text-sm leading-reading"><p className="text-xs font-medium text-warn">{t('ui.academy.steps.scaffoldLabel')}</p><p className="mt-1 whitespace-pre-wrap">{feedback.scaffold}</p></div> : null}
    {feedback?.numberLine ? <AcademyNumberLine min={feedback.numberLine.min} max={feedback.numberLine.max} value={feedback.numberLine.value} /> : null}
    {feedback?.correct ? <Button size="sm" onClick={onNext}>{t('ui.academy.steps.next')}</Button> : <form className="space-y-2"
      onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
      onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}
      onSubmit={event => {event.preventDefault(); submit()}}>
      <div role="group" aria-label={t('ui.academy.steps.confidence.label')} className="flex flex-wrap items-center gap-1">
        <span className="mr-1 text-xs text-muted">{t('ui.academy.steps.confidence.label')}</span>
        {(['guess', 'unsure', 'sure'] as const).map(level => <button key={level} type="button" data-confidence={level} aria-pressed={confidence === level} disabled={locked}
          onClick={() => setConfidence(level)}
          className={'rounded-full border px-2.5 py-1 text-xs ' + (confidence === level ? 'border-brand bg-brand/10 font-medium text-brand' : 'border-line text-muted')}>{t('ui.academy.steps.confidence.' + level)}</button>)}
      </div>
      {feedback && !feedback.correct ? <label className="block space-y-1 text-sm">
        <span className="text-xs font-medium text-warn">{t('ui.academy.steps.diagnosisPrompt')}</span>
        <textarea className="ui-field min-h-16 w-full" value={diagnosis} disabled={locked} placeholder={t('ui.academy.steps.diagnosisHint')} aria-label={t('ui.academy.steps.diagnosisPrompt')} onChange={event => setDiagnosis(event.target.value)} />
      </label> : null}
      {step.kind === 'explain' ? <div className="flex items-start gap-2">
        <textarea className="ui-field min-h-20 w-full" value={text} disabled={locked} placeholder={t('ui.academy.steps.explainPlaceholder')} aria-label={t('ui.academy.steps.explainPrompt')} onChange={event => setText(event.target.value)} />
        <AcademyVoiceButton active={!locked} disabled={locked} onText={value => setText(previous => previous ? previous + ' ' + value : value)} />
      </div> : null}
      {step.kind === 'numeric' ? <div className="space-y-2">
        <input type="number" className="ui-field w-32" value={text} disabled={locked} placeholder={t('ui.academy.steps.numericPlaceholder')} onChange={event => setText(event.target.value)} />
        {step.number_line ? <AcademyNumberLine min={step.number_line.min} max={step.number_line.max} value={Number(text) || step.number_line.min} onChange={locked ? undefined : v => setText(String(v))} /> : null}
      </div> : null}
      {step.kind === 'choice' ? <fieldset className="space-y-2" disabled={locked}>{(step.options ?? []).map(option => <label key={option.id} className="flex cursor-pointer items-start gap-2 rounded-lg border border-line p-3 text-sm focus-within:border-brand">
        <input type="radio" name={name} value={option.id} checked={choice === option.id} className="mt-1 accent-brand" onChange={() => setChoice(option.id)} />
        <span className="min-w-0 break-words">{option.label}</span></label>)}</fieldset> : null}
      {step.kind === 'ordering' ? <ol className="space-y-2" data-ordering-list>{order.map((id, index) => {const item = (step.items ?? []).find(entry => entry.id === id); return <li key={id} data-order-index={index} draggable={!locked}
        onDragStart={() => {dragIndex.current = index}} onDragOver={event => event.preventDefault()} onDrop={() => dropOn(index)} onDragEnd={() => {dragIndex.current = null}}
        className="flex cursor-grab items-center gap-2 rounded-lg border border-line p-2 text-sm active:cursor-grabbing">
        <GripVertical className="h-4 w-4 shrink-0 text-muted" aria-hidden />
        <span className="min-w-0 flex-1 break-words">{item?.label}</span>
        <Button type="button" size="xs" variant="ghost" disabled={locked || index === 0} aria-label={t('ui.academy.steps.moveUp')} onClick={() => move(index, -1)} icon={<ArrowUp className="h-3.5 w-3.5" />} />
        <Button type="button" size="xs" variant="ghost" disabled={locked || index === order.length - 1} aria-label={t('ui.academy.steps.moveDown')} onClick={() => move(index, 1)} icon={<ArrowDown className="h-3.5 w-3.5" />} />
      </li>})}</ol> : null}
      {step.kind === 'matching' ? <div className="space-y-3" data-matching-board>
        <div className="flex flex-wrap gap-2">{(step.left ?? []).map(item => <button key={item.id} type="button" data-match-left={item.id} disabled={locked} aria-pressed={selectedLeft === item.id}
          onClick={() => setSelectedLeft(previous => previous === item.id ? null : item.id)}
          className={'flex min-w-24 flex-col items-start gap-1 rounded-lg border p-2 text-sm ' + (selectedLeft === item.id ? 'border-brand bg-brand/10' : pairs[item.id] ? 'border-ok/40' : 'border-line')}>
          <span className="break-words">{item.label}</span>
          <span className={'text-xs ' + (pairs[item.id] ? 'text-ok' : 'text-muted')}>{pairs[item.id] ? (step.right ?? []).find(option => option.id === pairs[item.id])?.label : t('ui.academy.steps.matchEmpty')}</span>
        </button>)}</div>
        <div className="flex flex-wrap gap-2">{(step.right ?? []).map(option => <button key={option.id} type="button" data-match-right={option.id} disabled={locked || Object.values(pairs).includes(option.id)}
          onClick={() => assignRight(option.id)}
          className="rounded-full border border-line px-3 py-1.5 text-sm hover:border-brand disabled:opacity-40">{option.label}</button>)}</div>
      </div> : null}
      <Button type="submit" size="sm" disabled={locked || !ready} icon={<Send className="h-3.5 w-3.5" />}>{t('ui.academy.steps.submit')}</Button>
    </form>}
  </div>
}

/** 步骤播放器容器：一次一步、即时反馈、进度指示；未知回执只回查。 */
export function AcademySteps({projectId, lesson, active, disabled, onCompleted, onCalibration}: {projectId: string; lesson: AcademyLesson; active: boolean; disabled: boolean; onCompleted?: () => void; onCalibration?: (code: string) => void}) {
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

  useEffect(() => {if (view?.completed) onCompleted?.()}, [view?.completed, onCompleted])

  if (!steps?.length) return null
  const responses = view?.responses ?? []
  const done = new Set(responses.filter(response => response.correct).map(response => response.step_id))
  const total = steps.length
  const current = academyNextStep(steps, responses)
  const currentResponse = current ? responses.find(item => item.step_id === current.id) : undefined
  const currentIndex = current ? steps.findIndex(step => step.id === current.id) : -1
  const completed = view?.completed || current === null
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
    setFeedback({correct: result.correct, text: result.feedback ?? t(result.correct ? 'ui.academy.steps.correct' : 'ui.academy.steps.tryAgain'), scaffold: result.scaffold ?? undefined, numberLine: result.number_line ?? undefined, calibration: result.calibration ?? undefined})
    if (result.calibration) onCalibration?.(result.calibration)
    if (result.completed) setRevision(value => value + 1)
  }
  const submit = async (response: string | number | string[] | Record<string, string>, confidence: AcademyConfidence, diagnosis: string) => {
    if (disabled || busy || pending || !current) return
    const requestId = crypto.randomUUID()
    try {sessionStorage.setItem(pendingKey, JSON.stringify({requestId, projectId}))} catch {setError({key: 'ui.academy.error.store-invalid'})}
    setPending({requestId}); setBusy(true); setError(''); setFeedback(null)
    const outcome = await academyApi.stepAnswer(projectId, {request_id: requestId, lesson_id: lesson.id, step_id: current.id, response, confidence, diagnosis})
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
      : current ? <AcademyStepCard key={current.id + ':' + revision} step={current} busy={busy || disabled || pending !== null} feedback={feedback} cheer={cheer} blindspot={currentResponse?.blindspot} onSubmit={(response, confidence, diagnosis) => void submit(response, confidence, diagnosis)} onNext={next} /> : null}
  </div>
}
