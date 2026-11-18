import {useEffect, useId, useRef, useState} from 'react'
import {t, useLocale} from '@/i18n'
import type {AcademyCourse, AcademyEngagement, AcademyMastery as AcademyMasteryView, AcademyProject, AcademyReceipt, AcademyReview, AcademyReviewItem, AcademySkillState, AcademyStudyAnswer, NoticeText} from '@/types'
import {academyApi, academyBadges, academyMasteryCounts, academyReceiptMatches, academyReviewAnswers, academyReviewComplete, isAcademyUnconfirmedWrite} from '@/lib/academy'
import {ACADEMY_GOAL_EVENT, academyDailyGoalRead, academyTodayKey} from '@/lib/academyGoal'
import {noticeText} from '@/lib/noticeText'
import {cn} from '@/lib/utils'
import {AcademySkillMap} from './AcademySkillMap'
import {AcademyDailyGoal} from './AcademyDailyGoal'
import {AcademyMistakeJournal} from './AcademyMistakeJournal'
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
    {skill.evidence_count > 0 && (skill.reviews.delayed_attempts ?? 0) > 0 ? <p className="mt-1 text-xs text-ok" data-skill-delayed={skill.reviews.delayed_correct}>{t('ui.academy.mastery.delayedEvidence', {count: skill.reviews.delayed_correct})} · {t('ui.academy.mastery.delayedRate', {percent: Math.round(skill.reviews.delayed_correct / skill.reviews.delayed_attempts * 100)})}</p> : null}
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

/** 独立重测卡片流：一次一题单焦点检索，进度点＋键盘 1-9 选项/Enter 继续或提交；只有题干与选项，没有答案、反馈与提示；选择按题目身份保存，全部作答后显式提交。 */
export function AcademyReviewCard({item, busy, onSubmit}: {item: AcademyReviewItem; busy: boolean; onSubmit: (answers: AcademyStudyAnswer[]) => void}) {
  useLocale()
  const [selections, setSelections] = useState<Record<string, string>>({})
  const [confidences, setConfidences] = useState<Record<string, string>>({})
  const [index, setIndex] = useState(0)
  const composing = useRef(false)
  const mounted = useRef(false)
  const cardRef = useRef<HTMLFormElement>(null)
  const complete = academyReviewComplete(item, selections)
  const calibrated = item.checks.every(row => confidences[row.id])
  const check = item.checks[index] ?? item.checks[0]
  const answered = Boolean(check && selections[check.id])
  const currentConfidence = check ? confidences[check.id] : undefined
  const remaining = item.checks.some((row, position) => position !== index && !selections[row.id])
  /** 切题即聚焦新题首个选项（挂载时不抢焦点），键盘 1-9 直接作答。 */
  useEffect(() => {
    if (mounted.current) cardRef.current?.querySelector<HTMLInputElement>('input[type="radio"]')?.focus()
    else mounted.current = true
  }, [index])
  if (!check) return null
  const advance = () => {
    const after = item.checks.findIndex((row, position) => position > index && !selections[row.id])
    const target = after >= 0 ? after : item.checks.findIndex(row => !selections[row.id])
    if (target >= 0 && target !== index) setIndex(target)
  }
  const select = (option: string) => setSelections(previous => ({...previous, [check.id]: option}))
  return <form ref={cardRef} data-academy-review-lesson={item.lesson_id} data-review-card className="space-y-4 rounded-xl border border-line p-4"
    onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
    onKeyDown={event => {
      if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) {event.preventDefault(); return}
      const target = event.target as HTMLElement
      if (composing.current || busy || target.closest('textarea, select, input:not([type="radio"]), button')) return
      if (/^[1-9]$/.test(event.key)) {
        const option = check.options[Number(event.key) - 1]
        if (option) {event.preventDefault(); select(option.id)}
        return
      }
      if (event.key === 'Enter') {
        event.preventDefault()
        if (complete && calibrated) onSubmit(academyReviewAnswers(item, selections, confidences))
        else if (answered && currentConfidence) advance()
      }
    }}
    onSubmit={event => {event.preventDefault(); if (!composing.current && !busy && complete && calibrated) onSubmit(academyReviewAnswers(item, selections, confidences))}}>
    <div className="space-y-1">
      <h4 className="break-words text-sm font-medium">{item.title}</h4>
      <p className="text-xs leading-reading text-muted">{item.goal}</p>
      <p className="text-xs text-warn">{t('ui.academy.mastery.overdue', {days: item.overdue_days})}</p>
    </div>
    {/* 检索透明化：把 FSRS 调度器的"为什么现在重测"可见化（间隔效应／合意困难）。 */}
    <div data-review-rationale className="space-y-1 rounded-lg bg-canvas p-2">
      <div className="flex items-center gap-2">
        <span className="shrink-0 text-xs text-muted">{t('ui.academy.mastery.retentionLabel')}</span>
        <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-line" aria-hidden>
          <div data-review-retention className={cn('h-full rounded-full', item.retrievability >= 0.8 ? 'bg-ok' : item.retrievability >= 0.5 ? 'bg-warn' : 'bg-danger')} style={{width: Math.max(2, Math.min(100, Math.round(item.retrievability * 100))) + '%'}} />
        </div>
        <span className="shrink-0 text-xs font-medium">{Math.round(item.retrievability * 100)}%</span>
      </div>
      <p className="text-xs leading-reading text-muted">{t('ui.academy.mastery.rationale', {percent: Math.round(item.retrievability * 100)})}</p>
    </div>
    {item.checks.length > 1 ? <ol data-review-progress className="flex flex-wrap items-center gap-1.5" aria-label={t('ui.academy.mastery.progress')}>
      {item.checks.map((row, position) => <li key={row.id}>
        <button type="button" data-review-dot={position} aria-current={position === index ? 'step' : undefined} aria-label={t('ui.academy.mastery.questionN', {n: position + 1})}
          onClick={() => setIndex(position)}
          className={cn('h-3 w-3 rounded-full border transition-colors', position === index ? 'border-brand bg-brand' : selections[row.id] ? 'border-ok/60 bg-ok/50' : 'border-line bg-line')} />
      </li>)}
    </ol> : null}
    <div key={check.id} className="academy-card-enter space-y-3">
      <ReviewCheck check={check} value={selections[check.id] ?? ''} disabled={busy} onSelect={select} />
      <div role="group" aria-label={t('ui.academy.steps.confidence.label')} className="flex flex-wrap items-center gap-1">
        <span className="mr-1 text-xs text-muted">{t('ui.academy.steps.confidence.label')}</span>
        {(['guess', 'unsure', 'sure'] as const).map(level => <button key={level} type="button" data-confidence={level} aria-pressed={confidences[check.id] === level} disabled={busy}
          onClick={() => {setConfidences(previous => ({...previous, [check.id]: level})); requestAnimationFrame(() => {const action = cardRef.current?.querySelector<HTMLButtonElement>('[data-review-continue]') ?? cardRef.current?.querySelector<HTMLButtonElement>('button[type="submit"]'); action?.focus()})}}
          className={cn('rounded-full border px-2.5 py-1 text-xs', confidences[check.id] === level ? 'border-brand bg-brand/10 font-medium text-brand' : 'border-line text-muted')}>{t('ui.academy.steps.confidence.' + level)}</button>)}
      </div>
    </div>
    <div className="flex flex-wrap items-center gap-2">
      {item.checks.length > 1 ? <>
        <Button type="button" size="xs" variant="ghost" data-review-prev disabled={busy || index === 0} onClick={() => setIndex(value => value - 1)}>{t('ui.academy.mastery.prev')}</Button>
        <span className="text-xs text-muted">{t('ui.academy.mastery.questionOf', {n: index + 1, total: item.checks.length})}</span>
        <Button type="button" size="xs" variant="ghost" data-review-next disabled={busy || index === item.checks.length - 1} onClick={() => setIndex(value => value + 1)}>{t('ui.academy.mastery.next')}</Button>
      </> : null}
      <div className="ml-auto flex gap-2">
        {answered && currentConfidence && remaining ? <Button type="button" size="sm" variant="subtle" data-review-continue onClick={advance}>{t('ui.academy.mastery.continue')}</Button> : null}
        <Button type="submit" size="sm" disabled={busy || !complete || !calibrated}>{t('ui.academy.mastery.submit')}</Button>
      </div>
    </div>
  </form>
}

/** 记忆仪表盘与延迟重测入口；隐藏或收起时停止读取，未知回执只按原请求号回查。 */
/** 学习报告：把持久证据聚合成可读统计（掌握/盲点修复/迁移/延迟重测），供学习者与家长教师查看。 */
export function AcademyReport({mastery}: {mastery: AcademyMasteryView}) {
  useLocale()
  const counts = academyMasteryCounts(mastery)
  const [goal, setGoal] = useState<number | null>(() => academyDailyGoalRead())
  useEffect(() => {
    const onGoal = () => setGoal(academyDailyGoalRead())
    window.addEventListener(ACADEMY_GOAL_EVENT, onGoal)
    return () => window.removeEventListener(ACADEMY_GOAL_EVENT, onGoal)
  }, [])
  const delayedAttempts = mastery.reviews.delayed_attempts ?? 0
  const delayed = delayedAttempts ? Math.round((mastery.reviews.delayed_correct / delayedAttempts) * 100) : 0
  const immediateAttempts = Math.max(0, mastery.reviews.attempts - delayedAttempts)
  const immediateCorrect = Math.max(0, mastery.reviews.correct - mastery.reviews.delayed_correct)
  const immediateRate = immediateAttempts ? Math.round((immediateCorrect / immediateAttempts) * 100) : null
  const delayedRate = delayedAttempts ? Math.round((mastery.reviews.delayed_correct / delayedAttempts) * 100) : null
  const transferAttempts = mastery.transfer_attempts ?? 0
  const transferRate = transferAttempts ? Math.round(((mastery.transfers ?? 0) / transferAttempts) * 100) + '%' : null
  const tiles: Array<[string | number, string]> = [
    [counts.durable, 'ui.academy.report.durable'], [counts.learning, 'ui.academy.report.learning'], [counts.fresh, 'ui.academy.report.fresh'],
    [mastery.repairs ?? 0, 'ui.academy.report.repairs'], [mastery.blindspots_open ?? 0, 'ui.academy.report.blindspots'], [mastery.transfers ?? 0, 'ui.academy.report.transfers'],
    ...(transferRate ? [[transferRate, 'ui.academy.report.transferRate'] as [string, string]] : []),
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
    {/* 保持率对比：即时 vs 延迟（间隔≥1天）正确率，把测试效应可见化；延迟保持才是长期理解证据。 */}
    <div data-academy-retention className="space-y-2 rounded-xl border border-line p-3">
      <p className="text-xs font-medium">{t('ui.academy.report.retentionTitle')}</p>
      <p className="text-xs leading-reading text-muted">{t('ui.academy.report.retentionHint')}</p>
      {immediateRate == null && delayedRate == null ? <p className="text-xs text-muted">{t('ui.academy.report.retentionEmpty')}</p> : <div className="space-y-2">
        <div data-retention-immediate className="space-y-0.5">
          <div className="flex items-center justify-between text-xs"><span className="text-muted">{t('ui.academy.report.immediate')}</span><span className="font-medium">{immediateRate == null ? '—' : immediateRate + '%'} <span className="text-muted">({immediateCorrect}/{immediateAttempts})</span></span></div>
          <div className="h-1.5 overflow-hidden rounded-full bg-line" aria-hidden><div className="h-full rounded-full bg-brand" style={{width: (immediateRate ?? 0) + '%'}} /></div>
        </div>
        <div data-retention-delayed className="space-y-0.5">
          <div className="flex items-center justify-between text-xs"><span className="text-muted">{t('ui.academy.report.delayedRate')}</span><span className="font-medium">{delayedRate == null ? '—' : delayedRate + '%'} <span className="text-muted">({mastery.reviews.delayed_correct}/{delayedAttempts})</span></span></div>
          <div className="h-1.5 overflow-hidden rounded-full bg-line" aria-hidden><div className="h-full rounded-full bg-ok" style={{width: (delayedRate ?? 0) + '%'}} /></div>
        </div>
      </div>}
    </div>
    {mastery.activity?.length ? <div className="space-y-1">
      <p className="text-xs text-muted">{t('ui.academy.report.activity')}</p>
      {/* 近 14 天证据热图：强度分级，今日描边，设定目标后达成日绿环。 */}
      <div className="flex flex-wrap gap-1" role="img" aria-label={t('ui.academy.report.activity')} data-academy-activity>
        {mastery.activity.map(day => {
          const level = day.evidence <= 0 ? 0 : day.evidence <= 2 ? 1 : day.evidence <= 4 ? 2 : 3
          const today = day.date === academyTodayKey()
          const attained = goal != null && day.evidence >= goal
          return <span key={day.date} data-activity-day={day.date} title={day.date + ': ' + day.evidence}
            className={cn('h-4 w-4 rounded-sm', level === 0 ? 'bg-line' : level === 1 ? 'bg-brand/30' : level === 2 ? 'bg-brand/60' : 'bg-brand', attained ? 'ring-1 ring-ok' : today ? 'ring-1 ring-brand' : undefined)} />
        })}
      </div>
    </div> : null}
    <Button size="xs" variant="ghost" data-academy-export onClick={exportEvidence}>{t('ui.academy.report.export')}</Button>
  </div>
}

/** 独立重测会话：到期队列逐题作答、幂等回执、未知只回查；面板与专注舞台共用同一行为。 */
export function AcademyReviewSession({projectId, active, disabled = false, onCompleted, onOpenLesson, mode = 'due'}: {projectId: string; active: boolean; disabled?: boolean; onCompleted?: () => void; onOpenLesson?: (lessonId: string) => void; mode?: 'due' | 'cumulative'}) {
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
      const reviews = await (mode === 'cumulative' ? academyApi.cumulative(projectId, controller.signal) : academyApi.reviews(projectId, controller.signal))
      if (controller.signal.aborted) return
      if (reviews.ok) {setQueue(reviews.data.reviews); setLoaded(true)}
      else setError(reviews.copy ?? reviews.message)
    })()
    return () => controller.abort()
  }, [active, projectId, revision, mode])

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
  const submit = async (answers: AcademyStudyAnswer[]) => {
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
      {!result.review.correct && onOpenLesson ? <Button size="xs" variant="subtle" data-academy-restudy onClick={() => onOpenLesson(result.review.lesson_id)}>{t('ui.academy.mastery.restudy')}</Button> : null}
    </div> : null}
    {queue.length ? <Section title={t(mode === 'cumulative' ? 'ui.academy.mastery.cumulative' : 'ui.academy.mastery.reviewHeading')}>
      {mode === 'cumulative' ? <p className="mb-2 text-xs leading-reading text-muted">{t('ui.academy.mastery.cumulativeHint')}</p> : queue.length > 1 ? <p className="mb-2 text-xs leading-reading text-muted">{t('ui.academy.mastery.interleavedHint')}</p> : null}
      <p className="mb-2 text-xs leading-reading text-muted">{t('ui.academy.mastery.reviewHint')}</p>
      <AcademyReviewCard key={queue[0].lesson_id + ':' + revision} item={queue[0]} busy={busy || disabled || pending !== null} onSubmit={answers => void submit(answers)} />
    </Section> : loaded ? <p className="text-sm leading-reading text-muted">{t('ui.academy.mastery.queueEmpty')}</p> : active ? <p role="status" className="flex gap-2 text-sm text-muted"><Spinner />{t('ui.academy.loading')}</p> : null}
  </div>
}
export function AcademyMastery({projectId, active, onOpenLesson, course, progress, activeLessonId, recommendedId}: {projectId: string; active: boolean; onOpenLesson?: (lessonId: string) => void; course?: AcademyCourse; progress?: AcademyProject['progress']; activeLessonId?: string; recommendedId?: string}) {
  useLocale()
  const [open, setOpen] = useState(false)
  const [cumulative, setCumulative] = useState(false)
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
        <AcademyDailyGoal activity={view.activity} />
        {course ? <AcademySkillMap lessons={course.lessons} progress={progress ?? []} skills={view.skills} activeLessonId={activeLessonId} recommendedId={recommendedId} onOpenLesson={onOpenLesson} /> : null}
        <AcademySkillList skills={view.skills} />
        <AcademyMistakeJournal mistakes={view.mistakes} onOpenLesson={onOpenLesson} />
      </> : active ? <p role="status" className="flex gap-2 text-sm text-muted"><Spinner />{t('ui.academy.loading')}</p> : null}
      {view ? <AcademyReport mastery={view} /> : null}
      <Button size="xs" variant="ghost" data-academy-cumulative-toggle aria-pressed={cumulative} onClick={() => setCumulative(value => !value)}>{t(cumulative ? 'ui.academy.mastery.backToDue' : 'ui.academy.mastery.cumulative')}</Button>
      <AcademyReviewSession projectId={projectId} active={active && open} onCompleted={() => setRevision(value => value + 1)} onOpenLesson={onOpenLesson} mode={cumulative ? 'cumulative' : 'due'} />
    </div>
  </details>
}
