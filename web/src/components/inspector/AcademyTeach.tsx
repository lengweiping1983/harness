import {useEffect, useRef, useState} from 'react'
import {Send} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import type {AcademyLesson, AcademyReceipt, AcademyTeachQuiz, AcademyTutorTurn, NoticeText} from '@/types'
import {academyApi, academyReceiptMatches, academyTeachMisconception, academyTeachTurns, isAcademyUnconfirmedWrite, watchAcademyTeachQuizzes} from '@/lib/academy'
import {noticeText} from '@/lib/noticeText'
import {Markdown} from '@/components/markdown/Markdown'
import {Button, Spinner} from '@/components/ui/primitives'

type PendingTeach = {kind: 'turn' | 'quiz'; requestId: string; turnId?: string}

/** 学生测验结果；逐题身份来自宿主评分，不展示正确答案。 */
export function AcademyQuizResult({quiz}: {quiz: AcademyTeachQuiz}) {
  useLocale()
  if (quiz.status === 'running') return <p role="status" className="flex gap-2 text-sm text-muted"><Spinner />{t('ui.academy.teach.quizRunning')}</p>
  if (quiz.status !== 'completed') return <p className="text-sm text-warn">{t('ui.academy.teach.pending')}</p>
  return <div role="status" className="space-y-1 rounded-lg bg-canvas p-3 text-sm">
    <p className={quiz.correct ? 'font-medium text-ok' : 'font-medium text-warn'}>{t(quiz.correct ? 'ui.academy.teach.quizCorrect' : 'ui.academy.teach.quizWrong')}</p>
    <ul className="space-y-1 text-xs text-muted">{quiz.results.map((item, index) => <li key={item.check_id}>{t('ui.academy.mastery.checkEntry', {number: index + 1, status: t(item.correct ? 'ui.academy.mastery.checkCorrect' : 'ui.academy.mastery.checkWrong')})}</li>)}</ul>
  </div>
}

/** 教 AI 学生：教学对话复用带练观察列表，测验独立评分；未知写入只回查原请求。 */
export function AcademyTeach({projectId, lesson, fingerprint, turns, disabled, active, onRefresh, embedded = false, onCompleted}: {
  projectId: string; lesson: AcademyLesson; fingerprint: string; turns: AcademyTutorTurn[]
  disabled: boolean; active: boolean; onRefresh: () => void; embedded?: boolean; onCompleted?: () => void
}) {
  useLocale()
  const draftKey = 'academy.teach.draft.' + JSON.stringify([projectId, lesson.id])
  const pendingKey = 'academy.teach.pending.' + projectId
  const [open, setOpen] = useState(embedded)
  const [draft, setDraft] = useState(() => {try {return sessionStorage.getItem(draftKey) ?? ''} catch {return ''}})
  const [quizzes, setQuizzes] = useState<AcademyTeachQuiz[]>([])
  const [revision, setRevision] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<NoticeText | ''>('')
  const [pending, setPending] = useState<PendingTeach | null>(null)
  const composing = useRef(false)
  const teachTurns = academyTeachTurns(turns, lesson.id)
  const latest = teachTurns.at(-1)
  const misconception = latest?.misconception ?? academyTeachMisconception(lesson)
  const latestQuiz = latest ? quizzes.find(quiz => quiz.turn_id === latest.id) : undefined

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(pendingKey)
      if (!raw) return
      const value = JSON.parse(raw) as {kind?: unknown; requestId?: unknown; projectId?: unknown; turnId?: unknown}
      if (value && (value.kind === 'turn' || value.kind === 'quiz') && typeof value.requestId === 'string'
          && /^[A-Za-z0-9_-]{1,128}$/.test(value.requestId) && value.projectId === projectId
          && (value.kind === 'turn' || typeof value.turnId === 'string')) {
        setPending({kind: value.kind, requestId: value.requestId, ...(value.kind === 'quiz' ? {turnId: value.turnId as string} : {})})
      } else sessionStorage.removeItem(pendingKey)
    } catch {
      try {sessionStorage.removeItem(pendingKey)} catch {setPending(null)}
    }
  }, [pendingKey, projectId])

  useEffect(() => {
    try {sessionStorage.setItem(draftKey, draft)} catch {setError({key: 'ui.academy.error.store-invalid'})}
  }, [draft, draftKey])

  useEffect(() => {
    if (!active || !open) return
    return watchAcademyTeachQuizzes(projectId, {
      publish: setQuizzes,
      failure: failure => setError(failure.copy ?? failure.message),
      pending: true,
    })
  }, [active, open, projectId, revision])

  useEffect(() => {if (latestQuiz?.status === 'completed' && latestQuiz.correct) onCompleted?.()}, [latestQuiz?.status, latestQuiz?.correct, onCompleted])

  const clearPending = () => {
    try {sessionStorage.removeItem(pendingKey)} catch {setPending(null)}
    setPending(null)
  }
  const applyReceipt = (receipt: AcademyReceipt, identity: PendingTeach) => {
    if (!academyReceiptMatches(receipt, identity.requestId, projectId)) {setError({key: 'ui.http.invalidResponse'}); return}
    if (receipt.status === 'unknown') return
    if (receipt.status === 'failed') {
      clearPending()
      setError({key: receipt.result.error_code ? 'ui.academy.error.' + receipt.result.error_code : 'ui.academy.error.invalid-request'})
      return
    }
    clearPending()
    if (identity.kind === 'turn') {setDraft(''); onRefresh()}
    setRevision(value => value + 1)
  }
  const remember = (identity: PendingTeach) => {
    try {sessionStorage.setItem(pendingKey, JSON.stringify({...identity, projectId}))} catch {setError({key: 'ui.academy.error.store-invalid'})}
    setPending(identity)
  }
  const sendTurn = async () => {
    const prompt = draft.trim()
    if (disabled || busy || pending || composing.current || !prompt) return
    const identity: PendingTeach = {kind: 'turn', requestId: crypto.randomUUID()}
    remember(identity); setBusy(true); setError('')
    const outcome = await academyApi.tutor(projectId, {request_id: identity.requestId, lesson_id: lesson.id,
      expected_fingerprint: fingerprint, prompt, allow_execution: false, require_execution: false, mode: 'teach'})
    if (outcome.ok) applyReceipt(outcome.data, identity)
    else if (isAcademyUnconfirmedWrite(outcome)) setError(outcome.copy ?? outcome.message)
    else {clearPending(); setError(outcome.copy ?? outcome.message)}
    setBusy(false)
  }
  const sendQuiz = async (turnId: string) => {
    if (disabled || busy || pending) return
    const identity: PendingTeach = {kind: 'quiz', requestId: crypto.randomUUID(), turnId}
    remember(identity); setBusy(true); setError('')
    const outcome = await academyApi.teachQuiz(projectId, turnId, {request_id: identity.requestId})
    if (outcome.ok) applyReceipt(outcome.data, identity)
    else if (isAcademyUnconfirmedWrite(outcome)) setError(outcome.copy ?? outcome.message)
    else {clearPending(); setError(outcome.copy ?? outcome.message)}
    setBusy(false)
  }
  const recheck = async (identity: PendingTeach) => {
    if (busy) return
    setBusy(true)
    const outcome = await academyApi.receipt(identity.requestId)
    if (outcome.ok) applyReceipt(outcome.data, identity)
    else setError(outcome.copy ?? outcome.message)
    setBusy(false)
  }

  const content = <div className={embedded ? 'space-y-4' : 'mt-3 space-y-4'}>
      <p className="text-xs leading-reading text-muted">{t('ui.academy.teach.hint')}</p>
      <p className="text-xs leading-reading text-brand">{t('ui.academy.teach.why')}</p>
      <ol className="flex flex-wrap gap-1 text-xs text-muted">{['loop1', 'loop2', 'loop3'].map(key => <li key={key} className="rounded-full border border-line px-2 py-0.5">{t('ui.academy.teach.' + key)}</li>)}</ol>
      {error ? <p role="alert" className="text-sm text-danger">{noticeText(error)}</p> : null}
      {pending ? <div role="status" className="space-y-2 rounded-xl border border-warn/40 p-3 text-sm">
        <p>{t('ui.academy.teach.pending')}</p>
        <p className="break-all font-mono text-xs">{t('ui.academy.receiptId', {id: pending.requestId})}</p>
        <Button size="xs" disabled={busy} onClick={() => void recheck(pending)}>{t('ui.academy.recheckReceipt')}</Button>
      </div> : null}
      {misconception ? <div className="rounded-lg border border-line p-3 text-sm">
        <p className="flex flex-wrap items-center gap-2 text-xs font-medium text-muted">{t('ui.academy.teach.misconception')}<span className="rounded-full bg-warn/15 px-2 py-0.5 text-warn">{t('ui.academy.teach.misconceptionTag')}</span></p>
        <p className="mt-1 whitespace-pre-wrap leading-reading">{misconception.prompt}</p>
        <p className="mt-1 whitespace-pre-wrap leading-reading text-warn">{t('ui.academy.teach.studentBelieves', {label: misconception.label})}</p>
      </div> : null}
      {teachTurns.length ? <ol className="space-y-3">{teachTurns.map(turn => <li key={turn.id} data-teach-turn-id={turn.id} className="space-y-2">
        <div className="rounded-lg bg-canvas p-3 text-sm"><p className="text-xs font-medium text-muted">{t('ui.academy.teach.learnerLabel')}</p><p className="mt-1 whitespace-pre-wrap leading-reading">{turn.prompt}</p></div>
        <div className="rounded-lg border border-line p-3 text-sm"><p className="text-xs font-medium text-muted">{t('ui.academy.teach.studentLabel')}</p>
          {turn.status === 'running' ? <p role="status" className="mt-1 flex gap-2 text-muted"><Spinner />{t('ui.academy.loading')}</p>
            : turn.status === 'completed' ? <div className="mt-1"><Markdown workspaceLinks={false}>{turn.response}</Markdown></div>
            : <p className="mt-1 text-warn">{t('ui.academy.teach.pending')}</p>}
        </div>
      </li>)}</ol> : <p className="text-sm leading-reading text-muted">{t('ui.academy.teach.empty')}</p>}
      {latest && latest.status === 'completed' ? latestQuiz ? <div className="space-y-2">
        <AcademyQuizResult quiz={latestQuiz} />
        {latestQuiz.status === 'failed' ? <Button size="xs" disabled={disabled || busy || pending !== null} onClick={() => void sendQuiz(latest.id)}>{t('ui.academy.teach.quizRetry')}</Button> : null}
        {latestQuiz.status === 'unknown' ? <Button size="xs" disabled={busy} onClick={() => void recheck({kind: 'quiz', requestId: latestQuiz.request_id, turnId: latest.id})}>{t('ui.academy.recheckReceipt')}</Button> : null}
      </div> : <Button size="sm" disabled={disabled || busy || pending !== null} onClick={() => void sendQuiz(latest.id)}>{t('ui.academy.teach.quizAction')}</Button> : null}
      <form className="space-y-2" onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
        onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}
        onSubmit={event => {event.preventDefault(); void sendTurn()}}>
        <textarea className="ui-field min-h-24 w-full" value={draft} placeholder={t('ui.academy.teach.placeholder')} aria-label={t('ui.academy.teach.placeholder')}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => {if (event.key === 'Enter' && !event.shiftKey && !event.altKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {event.preventDefault(); void sendTurn()}}} />
        <Button type="submit" size="sm" disabled={disabled || busy || pending !== null || !draft.trim()} icon={<Send className="h-3.5 w-3.5" />}>{t('ui.academy.teach.send')}</Button>
      </form>
    </div>
  if (embedded) return <div data-academy-section="teach">{content}</div>
  return <details data-academy-section="teach" open={open} onToggle={event => setOpen(event.currentTarget.open)} className="rounded-xl border border-line p-3">
    <summary className="cursor-pointer rounded-lg text-sm text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.teach.title')}</summary>
    {content}
  </details>
}
