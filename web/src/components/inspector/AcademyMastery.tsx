import {useEffect, useId, useRef, useState} from 'react'
import {t, useLocale} from '@/i18n'
import type {AcademyEngagement, AcademyMastery as AcademyMasteryView, AcademyReceipt, AcademyReview, AcademyReviewItem, AcademySkillState, NoticeText} from '@/types'
import {academyApi, academyBadges, academyMasteryCounts, academyReceiptMatches, academyReviewAnswers, academyReviewComplete, isAcademyUnconfirmedWrite} from '@/lib/academy'
import {noticeText} from '@/lib/noticeText'
import {Badge, Button, Section, Spinner} from '@/components/ui/primitives'

type PendingReview = {requestId: string; lessonId: string}


/** 认知参与度统计；没有证据时不显示，不投影为零参与。 */
export function AcademyEngagementStats({engagement}: {engagement: AcademyEngagement}) {
  useLocale()
  if (engagement.total === 0) return null
  return <p data-academy-engagement className="text-xs text-muted">{t('ui.academy.mastery.engagement', {constructive: engagement.constructive, active: engagement.active, percent: Math.round(engagement.constructive_ratio * 100)})}</p>
}

/** 技能记忆列表；没有证据的技能保持未开始，不投影为已掌握。 */
export function AcademySkillList({skills}: {skills: readonly AcademySkillState[]}) {
  const locale = useLocale()
  if (!skills.some(skill => skill.evidence_count > 0)) return <p className="text-sm leading-reading text-muted">{t('ui.academy.mastery.noEvidence')}</p>
  return <ul className="space-y-2">{skills.map(skill => <li key={skill.lesson_id} data-skill-id={skill.lesson_id} className="rounded-lg border border-line p-3 text-sm">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="min-w-0 break-words font-medium">{skill.title}</span>
      <Badge tone={skill.state === 'durable' ? 'ok' : skill.state === 'learning' ? 'warn' : 'neutral'}>{t('ui.academy.mastery.state.' + skill.state)}</Badge>
    </div>
    {skill.evidence_count > 0 ? <p className="mt-1 text-xs text-muted">{t('ui.academy.mastery.retrievability', {percent: Math.round(skill.retrievability * 100)})} · {t('ui.academy.mastery.stability', {days: skill.stability_days})}</p> : null}
    {skill.evidence_count > 0 && skill.due_at != null ? <p className="mt-1 text-xs text-warn">{t('ui.academy.mastery.nextDue', {date: new Date(skill.due_at * 1000).toLocaleDateString(locale === 'zh-CN' ? 'zh-CN' : 'en')})}</p> : null}
  </li>)}</ul>
}

function ReviewCheck({check, value, disabled, onSelect}: {check: AcademyReviewItem['checks'][number]; value: string; disabled: boolean; onSelect: (option: string) => void}) {
  const name = useId()
  return <fieldset disabled={disabled} className="space-y-2">
    <legend className="mb-2 whitespace-pre-wrap text-base leading-reading">{check.prompt}</legend>
    {check.options.map(option => <label key={option.id} className="flex cursor-pointer items-start gap-2 rounded-lg border border-line p-3 text-sm leading-reading focus-within:border-brand">
      <input type="radio" name={name} value={option.id} checked={value === option.id} className="mt-1 accent-brand" onChange={() => onSelect(option.id)} />
      <span className="min-w-0 break-words">{option.label}</span>
    </label>)}
  </fieldset>
}

/** 独立重测卡片：只有题干与选项，没有答案、反馈与提示；选择按题目身份保存。 */
export function AcademyReviewCard({item, busy, onSubmit}: {item: AcademyReviewItem; busy: boolean; onSubmit: (answers: Array<{check_id: string; option_id: string}>) => void}) {
  useLocale()
  const [selections, setSelections] = useState<Record<string, string>>({})
  const composing = useRef(false)
  const complete = academyReviewComplete(item, selections)
  return <form data-academy-review-lesson={item.lesson_id} className="space-y-3 rounded-xl border border-line p-3"
    onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
    onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}
    onSubmit={event => {event.preventDefault(); if (!composing.current && !busy && complete) onSubmit(academyReviewAnswers(item, selections))}}>
    <div className="space-y-1">
      <h4 className="break-words text-sm font-medium">{item.title}</h4>
      <p className="text-xs leading-reading text-muted">{item.goal}</p>
      <p className="text-xs text-warn">{t('ui.academy.mastery.overdue', {days: item.overdue_days})}</p>
    </div>
    {item.checks.map(check => <ReviewCheck key={check.id} check={check} value={selections[check.id] ?? ''} disabled={busy} onSelect={option => setSelections(previous => ({...previous, [check.id]: option}))} />)}
    <Button type="submit" size="sm" disabled={busy || !complete}>{t('ui.academy.mastery.submit')}</Button>
  </form>
}

/** 记忆仪表盘与延迟重测入口；隐藏或收起时停止读取，未知回执只按原请求号回查。 */
/** 学习报告：把持久证据聚合成可读统计（掌握/盲点修复/迁移/延迟重测），供学习者与家长教师查看。 */
export function AcademyReport({mastery}: {mastery: AcademyMasteryView}) {
  useLocale()
  const counts = academyMasteryCounts(mastery)
  const delayed = mastery.reviews.attempts ? Math.round((mastery.reviews.delayed_correct / mastery.reviews.attempts) * 100) : 0
  const tiles: Array<[string | number, string]> = [
    [counts.durable, 'ui.academy.report.durable'], [counts.learning, 'ui.academy.report.learning'], [counts.fresh, 'ui.academy.report.fresh'],
    [mastery.repairs ?? 0, 'ui.academy.report.repairs'], [mastery.blindspots_open ?? 0, 'ui.academy.report.blindspots'], [mastery.transfers ?? 0, 'ui.academy.report.transfers'],
    [delayed + '%', 'ui.academy.report.delayed'], [mastery.streak_days ?? 0, 'ui.academy.report.streak'],
    [mastery.calibration?.sure_correct ?? 0, 'ui.academy.report.sureCorrect'], [mastery.calibration?.sure_wrong ?? 0, 'ui.academy.report.sureWrong'],
    [mastery.calibration?.guess_correct ?? 0, 'ui.academy.report.guessCorrect'],
  ]
  /** 导出原始证据聚合为 JSON，供家长教师核对与实测研究；纯客户端下载，不上传。 */
  const exportEvidence = () => {
    const blob = new Blob([JSON.stringify(mastery, null, 2)], {type: 'application/json'})
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'harness-academy-evidence.json'
    anchor.click()
    URL.revokeObjectURL(url)
  }
  return <div className="space-y-2">
    <div data-academy-report aria-label={t('ui.academy.report.title')} className="grid gap-2 sm:grid-cols-4">
      {tiles.map(([value, key]) => <div key={key} className="rounded-xl border border-line p-3">
        <p className="text-lg font-semibold">{value}</p>
        <p className="text-xs text-muted">{t(key)}</p>
      </div>)}
    </div>
    {academyBadges(mastery).length ? <div className="flex flex-wrap gap-1" data-academy-badges>
      {academyBadges(mastery).map(badge => <span key={badge} className="rounded-full border border-ok/40 bg-ok/10 px-2 py-0.5 text-xs font-medium text-ok">{t('ui.academy.badge.' + badge)}</span>)}
    </div> : null}
    <Button size="xs" variant="ghost" data-academy-export onClick={exportEvidence}>{t('ui.academy.report.export')}</Button>
  </div>
}

/** 独立重测会话：到期队列逐题作答、幂等回执、未知只回查；面板与专注舞台共用同一行为。 */
export function AcademyReviewSession({projectId, active, disabled = false, onCompleted}: {projectId: string; active: boolean; disabled?: boolean; onCompleted?: () => void}) {
  useLocale()
  const [queue, setQueue] = useState<AcademyReviewItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [revision, setRevision] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<NoticeText | ''>('')
  const [result, setResult] = useState<{review: AcademyReview; skill: AcademySkillState} | null>(null)
  const [pending, setPending] = useState<PendingReview | null>(null)
  const pendingKey = 'academy.mastery.pending.' + projectId
  const completedRef = useRef(onCompleted); completedRef.current = onCompleted

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(pendingKey)
      if (!raw) return
      const value = JSON.parse(raw) as {requestId?: unknown; projectId?: unknown; lessonId?: unknown}
      if (value && typeof value.requestId === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value.requestId)
          && value.projectId === projectId && typeof value.lessonId === 'string') {
        setPending({requestId: value.requestId, lessonId: value.lessonId})
      } else sessionStorage.removeItem(pendingKey)
    } catch {
      try {sessionStorage.removeItem(pendingKey)} catch {setPending(null)}
    }
  }, [pendingKey, projectId])

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    void (async () => {
      const reviews = await academyApi.reviews(projectId, controller.signal)
      if (controller.signal.aborted) return
      if (reviews.ok) {setQueue(reviews.data.reviews); setLoaded(true)}
      else setError(reviews.copy ?? reviews.message)
    })()
    return () => controller.abort()
  }, [active, projectId, revision])

  useEffect(() => {if (loaded && queue.length === 0) completedRef.current?.()}, [loaded, queue.length])

  const clearPending = () => {
    try {sessionStorage.removeItem(pendingKey)} catch {setPending(null)}
    setPending(null)
  }
  const applyReceipt = (receipt: AcademyReceipt, identity: PendingReview) => {
    if (!academyReceiptMatches(receipt, identity.requestId, projectId)) {setError({key: 'ui.http.invalidResponse'}); return}
    if (receipt.status === 'unknown') return
    if (receipt.status === 'failed') {
      clearPending()
      setError({key: receipt.result.error_code ? 'ui.academy.error.' + receipt.result.error_code : 'ui.academy.error.invalid-request'})
      return
    }
    if (!receipt.result.review || !receipt.result.skill) {setError({key: 'ui.http.invalidResponse'}); return}
    clearPending()
    setResult({review: receipt.result.review, skill: receipt.result.skill})
    setRevision(value => value + 1)
  }
  const submit = async (answers: Array<{check_id: string; option_id: string}>) => {
    const item = queue[0]
    if (disabled || busy || pending || !item) return
    const identity: PendingReview = {requestId: crypto.randomUUID(), lessonId: item.lesson_id}
    try {sessionStorage.setItem(pendingKey, JSON.stringify({...identity, projectId}))} catch {setError({key: 'ui.academy.error.store-invalid'})}
    setPending(identity); setBusy(true); setError('')
    const outcome = await academyApi.review(projectId, {request_id: identity.requestId, lesson_id: item.lesson_id, answers})
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

  return <div data-academy-review-session className="space-y-3">
    {error ? <p role="alert" className="text-sm text-danger">{noticeText(error)}</p> : null}
    {pending ? <div role="status" className="space-y-2 rounded-xl border border-warn/40 p-3 text-sm">
      <p>{t('ui.academy.mastery.pending')}</p>
      <p className="break-all font-mono text-xs">{t('ui.academy.receiptId', {id: pending.requestId})}</p>
      <Button size="xs" disabled={busy} onClick={() => void recheck()}>{t('ui.academy.recheckReceipt')}</Button>
    </div> : null}
    {result ? <div role="status" className="space-y-1 rounded-lg bg-canvas p-3 text-sm">
      <p className={result.review.correct ? 'font-medium text-ok' : 'font-medium text-warn'}>{t(result.review.correct ? 'ui.academy.mastery.resultCorrect' : 'ui.academy.mastery.resultWrong')}</p>
      <ul className="space-y-1 text-xs text-muted">{result.review.results.map((item, index) => <li key={item.check_id}>{t('ui.academy.mastery.checkEntry', {number: index + 1, status: t(item.correct ? 'ui.academy.mastery.checkCorrect' : 'ui.academy.mastery.checkWrong')})}</li>)}</ul>
      <p className="text-xs text-muted">{t('ui.academy.mastery.stability', {days: result.skill.stability_days})}</p>
    </div> : null}
    {queue.length ? <Section title={t('ui.academy.mastery.reviewHeading')}>
      {queue.length > 1 ? <p className="mb-2 text-xs leading-reading text-muted">{t('ui.academy.mastery.interleavedHint')}</p> : null}
      <p className="mb-2 text-xs leading-reading text-muted">{t('ui.academy.mastery.reviewHint')}</p>
      <AcademyReviewCard key={queue[0].lesson_id + ':' + revision} item={queue[0]} busy={busy || disabled || pending !== null} onSubmit={answers => void submit(answers)} />
    </Section> : loaded ? <p className="text-sm leading-reading text-muted">{t('ui.academy.mastery.queueEmpty')}</p> : active ? <p role="status" className="flex gap-2 text-sm text-muted"><Spinner />{t('ui.academy.loading')}</p> : null}
  </div>
}
export function AcademyMastery({projectId, active}: {projectId: string; active: boolean}) {
  useLocale()
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<AcademyMasteryView | null>(null)
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState<NoticeText | ''>('')

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    void (async () => {
      const mastery = await academyApi.mastery(projectId, controller.signal)
      if (controller.signal.aborted) return
      if (mastery.ok) setView(mastery.data.mastery)
      else setError(mastery.copy ?? mastery.message)
    })()
    return () => controller.abort()
  }, [active, projectId, revision])

  return <details data-academy-section="mastery" open={open} onToggle={event => setOpen(event.currentTarget.open)} className="rounded-xl border border-line p-3">
    <summary className="cursor-pointer rounded-lg text-sm text-muted focus-visible:outline focus-visible:outline-brand">
      {t('ui.academy.mastery.title')}
      {view && view.due_count > 0 ? <span className="ml-2 rounded-full bg-warn/15 px-2 py-0.5 text-xs text-warn">{t('ui.academy.mastery.dueBadge', {count: view.due_count})}</span> : null}
    </summary>
    <div className="mt-3 space-y-4">
      <p className="text-xs leading-reading text-muted">{t('ui.academy.mastery.intro')}</p>
      {error ? <p role="alert" className="text-sm text-danger">{noticeText(error)}</p> : null}
      {view ? <>
        <p className="text-xs text-muted">{t('ui.academy.mastery.statAttempts', {count: view.reviews.attempts})} · {t('ui.academy.mastery.statCorrect', {count: view.reviews.correct})} · {t('ui.academy.mastery.statDelayed', {count: view.reviews.delayed_correct})}</p>
        <AcademyEngagementStats engagement={view.engagement} />
        <AcademySkillList skills={view.skills} />
      </> : active ? <p role="status" className="flex gap-2 text-sm text-muted"><Spinner />{t('ui.academy.loading')}</p> : null}
      {view ? <AcademyReport mastery={view} /> : null}
      <AcademyReviewSession projectId={projectId} active={active && open} onCompleted={() => setRevision(value => value + 1)} />
    </div>
  </details>
}
