import {useCallback, useEffect, useMemo, useRef, useState, type TouchEvent as ReactTouchEvent} from 'react'
import {Check, Flame, Send, Sparkles, X} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import type {AcademyCourse, AcademyLesson, AcademyMastery as AcademyMasteryView, AcademyProject, AcademyTutorTurn} from '@/types'
import {academyApi, academyResumeLesson} from '@/lib/academy'
import {academyStudioChecksDone, academyStudioCoachPrompt, academyStudioCoachTurns, academyStudioJourney, academyStudioNextPhase, academyStudioPhases, type AcademyStudioCoachLevel, type AcademyStudioPhaseId} from '@/lib/academyStudio'
import {cn} from '@/lib/utils'
import {Markdown} from '@/components/markdown/Markdown'
import {Button, Spinner} from '@/components/ui/primitives'
import {AcademyStruggle} from './AcademyStruggle'
import {AcademyWorld} from './AcademyWorld'
import {AcademySteps} from './AcademySteps'
import {AcademyTeach} from './AcademyTeach'
import {AcademyKnowledgeChecks} from './AcademyKnowledgeChecks'
import {AcademyReport, AcademyReviewSession} from './AcademyMastery'
import {AcademyVoiceButton} from './AcademyVoice'
import {AcademyDailyGoal} from './AcademyDailyGoal'

const phaseLabelKey: Record<AcademyStudioPhaseId, string> = {
  review: 'ui.academy.studio.phase.review', struggle: 'ui.academy.studio.phase.struggle', world: 'ui.academy.studio.phase.world', steps: 'ui.academy.studio.phase.steps',
  reading: 'ui.academy.studio.phase.reading', teach: 'ui.academy.studio.phase.teach', checks: 'ui.academy.quickCheck',
  note: 'ui.academy.studio.phase.note', finish: 'ui.academy.studio.phase.finish',
}
const coachLevelNames = ['try', 'hint', 'explain'] as const

/** 专注学习舞台：全屏单焦点学习界面。旅程路径（记忆光环）＋一次一个学习环节（方向键/触摸滑动切换）＋底部 AI 教练（自主性三档）；专注模式收敛界面 chrome。 */
export function AcademyStudio({course, project, lesson, active, disabled, tutorTurns, struggleRevealed, checkAnswers, note, studyReady, studyCompleted, onClose, onOpenLesson, onAskCoach, onStrugglePhase, onCheckAnswer, onNote, onComplete, onRefresh}: {
  course: AcademyCourse; project: AcademyProject; lesson: AcademyLesson
  active: boolean; disabled: boolean; tutorTurns: AcademyTutorTurn[]
  struggleRevealed: boolean; checkAnswers: ReadonlyMap<string, string>; note: string
  studyReady: boolean; studyCompleted: boolean
  onClose: () => void; onOpenLesson: (lessonId: string) => void; onAskCoach: (prompt: string) => void
  onStrugglePhase: (revealed: boolean) => void; onCheckAnswer: (id: string, answer: string) => void
  onNote: (value: string) => void; onComplete: () => void; onRefresh: () => void
}) {
  useLocale()
  const [dueCount, setDueCount] = useState(0)
  const [skills, setSkills] = useState<Readonly<Record<string, string>>>({})
  const [masteryView, setMasteryView] = useState<AcademyMasteryView | null>(null)
  const [cumulativeMode, setCumulativeMode] = useState(false)
  const [celebrate, setCelebrate] = useState(false)
  const [prefs, setPrefs] = useState<{large: boolean; reducedMotion: boolean; zen: boolean}>(() => {
    try {const raw = JSON.parse(localStorage.getItem('harness.academy.prefs.v1') ?? '{}'); return {large: raw.large === true, reducedMotion: raw.reducedMotion === true, zen: raw.zen === true}} catch {return {large: false, reducedMotion: false, zen: false}}
  })
  const updatePrefs = (next: {large: boolean; reducedMotion: boolean; zen: boolean}) => {
    setPrefs(next)
    try {localStorage.setItem('harness.academy.prefs.v1', JSON.stringify(next))} catch { /* 存储不可用时仅本次会话生效 */ }
  }
  const manual = useRef(false)
  const phases = useMemo(() => academyStudioPhases(lesson, dueCount > 0), [lesson, dueCount])
  const checks = lesson.knowledge_checks ?? []
  const checksDone = academyStudioChecksDone(checks, checkAnswers)
  /** 初始进度来自已有证据：struggle 揭示、快速测试、反思与结课状态，打开舞台即回到离开的位置。 */
  const [done, setDone] = useState<ReadonlySet<string>>(() => {
    const initial = new Set<string>()
    if (struggleRevealed) initial.add('struggle')
    if (checksDone) initial.add('checks')
    if (note.trim()) initial.add('note')
    if (studyCompleted) initial.add('finish')
    return initial
  })
  /** 会话连续性：重开舞台回到上次停留环节（按项目+课次持久化）。 */
  const phaseKey = 'harness.academy.phase.v1:' + JSON.stringify([project.id, lesson.id])
  const [phase, setPhase] = useState<AcademyStudioPhaseId>(() => {
    if (studyCompleted || studyReady) return 'finish'
    try {
      const saved = localStorage.getItem(phaseKey)
      if (saved && phases.some(item => item.id === saved)) return saved as AcademyStudioPhaseId
    } catch { /* 存储不可用时从首环节开始 */ }
    return phases[0].id
  })
  useEffect(() => {try {localStorage.setItem(phaseKey, phase)} catch { /* 忽略存储失败 */ }}, [phase, phaseKey])
  const [coachLevel, setCoachLevel] = useState<AcademyStudioCoachLevel>(0)
  const [nudge, setNudge] = useState<'struggle' | 'blindspot' | null>(() => struggleRevealed ? 'struggle' : null)
  const [coachDraft, setCoachDraft] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [visible, setVisible] = useState(() => typeof document === 'undefined' || !document.hidden)
  const composing = useRef(false)
  const phaseRef = useRef(phase); phaseRef.current = phase
  const doneRef = useRef(done); doneRef.current = done
  const closeRef = useRef(onClose); closeRef.current = onClose
  const dialog = useRef<HTMLDivElement | null>(null)
  const recommended = academyResumeLesson(course, project)
  const journey = academyStudioJourney(course.lessons, project.progress, lesson.id, skills, recommended?.id)
  const coachTurns = academyStudioCoachTurns(tutorTurns, project.id, lesson.id)
  const coachRunning = coachTurns.some(turn => turn.status === 'running')
  const latestCoachTurn = coachTurns.at(-1)
  /** 苏格拉底回应邀请：教练最近一次回答以问题收尾时，一键聚焦输入框回应。 */
  const awaitingReply = Boolean(latestCoachTurn && latestCoachTurn.status === 'completed' && /[?？]\s*$/.test(latestCoachTurn.response.trim()))
  const threadRef = useRef<HTMLOListElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const threadSignature = coachTurns.map(turn => turn.id + ':' + turn.status + ':' + (turn.partial_response?.length ?? 0) + ':' + turn.response.length).join('|')
  const lessonIndex = course.lessons.findIndex(item => item.id === lesson.id)
  const nextLesson = lessonIndex >= 0 ? course.lessons[lessonIndex + 1] : undefined
  const completedCount = course.lessons.filter(item => project.progress.some(row => row.lesson_id === item.id && row.state === 'completed')).length

  /** 标记环节完成；若正停留在该环节则自动进入下一未完成环节，保持单焦点流动。 */
  const markDone = useCallback((id: AcademyStudioPhaseId) => {
    if (!phases.some(item => item.id === id) || doneRef.current.has(id)) return
    const next = new Set(doneRef.current); next.add(id)
    doneRef.current = next; setDone(next)
    if (phaseRef.current === id) {
      const target = academyStudioNextPhase(phases, next, id)
      if (target) {manual.current = true; setPhase(target)}
    }
  }, [phases])

  useEffect(() => {if (struggleRevealed) markDone('struggle')}, [struggleRevealed, markDone])
  /** 主动介入仅由宿主证据触发：挣扎揭示或高确信错误；不自动调用模型，学习者一键采纳或忽略。 */
  useEffect(() => {if (struggleRevealed) setNudge('struggle')}, [struggleRevealed])
  useEffect(() => {if (checksDone) markDone('checks')}, [checksDone, markDone])
  useEffect(() => {if (note.trim()) markDone('note')}, [note, markDone])
  useEffect(() => {if (studyCompleted) markDone('finish')}, [studyCompleted, markDone])
  /** 完成庆祝：短暂彩带爆发，尊重减少动效偏好；不阻塞交互。 */
  useEffect(() => {
    if (!studyCompleted || prefs.reducedMotion) return
    setCelebrate(true)
    const timer = setTimeout(() => setCelebrate(false), 1600)
    return () => clearTimeout(timer)
  }, [studyCompleted, prefs.reducedMotion])

  /** 到期独立重测优先于新内容：掌握学习先补旧缺口再前进。 */
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    void (async () => {
      const mastery = await academyApi.mastery(project.id, controller.signal)
      if (controller.signal.aborted) return
      if (mastery.ok) {setMasteryView(mastery.data.mastery); setDueCount(mastery.data.mastery.due_count); setSkills(Object.fromEntries(mastery.data.mastery.skills.map(skill => [skill.lesson_id, skill.state])))}
    })()
    return () => controller.abort()
  }, [active, project.id])
  useEffect(() => {if (dueCount > 0 && !manual.current && !doneRef.current.has('review')) setPhase('review')}, [dueCount])

  /** 专注计时：隐藏标签页暂停计时与轮询，回到前台继续；到预计用时给出休息提示。 */
  useEffect(() => {
    const onVisibility = () => setVisible(!document.hidden)
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [])
  useEffect(() => {
    if (!active || !visible) return
    const timer = setInterval(() => setElapsed(value => value + 1), 1000)
    return () => clearInterval(timer)
  }, [active, visible])

  /** 对话流自动滚动到最新内容；仅内容签名变化时滚动，尊重减少动效偏好。 */
  useEffect(() => {
    const el = threadRef.current
    if (el) el.scrollTo({top: el.scrollHeight, behavior: prefs.reducedMotion ? 'auto' : 'smooth'})
  }, [threadSignature, prefs.reducedMotion])

  /** 键盘-first：切换环节即聚焦该环节主输入（输入框优先，其次首个可用按钮），零鼠标流转。 */
  useEffect(() => {
    const stage = dialog.current?.querySelector('[data-studio-phase="' + phase + '"]')
    const target = stage?.querySelector('input:not([disabled]), textarea:not([disabled])') ?? stage?.querySelector('button:not([disabled])')
    if (target instanceof HTMLElement) target.focus()
  }, [phase])
  useEffect(() => {
    dialog.current?.focus()
    const onKey = (event: KeyboardEvent) => {if (event.key === 'Escape' && !composing.current) closeRef.current()}
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  /** 对话式快捷追问：按当前环节给出苏格拉底式低摩擦追问 chips，点击即以对应自主档发送。 */
  const coachSuggestions: Array<{key: string; level: AcademyStudioCoachLevel}> = phase === 'steps'
    ? [{key: 'hint', level: 1}, {key: 'whyWrong', level: 1}, {key: 'simpler', level: 2}]
    : phase === 'review'
      ? [{key: 'whyDue', level: 2}, {key: 'hint', level: 1}]
      : [{key: 'explain', level: 2}, {key: 'hint', level: 1}]
  /** 触摸滑动切换环节：与方向键等价；起点落在交互控件上时不拦截，避免与拖拽作答冲突。 */
  const swipeStart = useRef<{x: number; y: number} | null>(null)
  const onSwipeStart = (event: ReactTouchEvent<HTMLElement>) => {
    const target = event.target as HTMLElement
    if (target.closest('input, textarea, select, button, a, [role="slider"], [data-ordering-list], [data-matching-board], [data-academy-world]')) {swipeStart.current = null; return}
    const touch = event.touches[0]
    swipeStart.current = touch ? {x: touch.clientX, y: touch.clientY} : null
  }
  const onSwipeEnd = (event: ReactTouchEvent<HTMLElement>) => {
    const start = swipeStart.current; swipeStart.current = null
    const touch = event.changedTouches[0]
    if (!start || !touch || composing.current) return
    const dx = touch.clientX - start.x, dy = touch.clientY - start.y
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return
    const index = phases.findIndex(item => item.id === phase)
    const next = phases[index + (dx < 0 ? 1 : -1)]
    if (next) {manual.current = true; setPhase(next.id)}
  }
  const sendCoach = () => {
    if (composing.current || disabled || coachRunning) return
    onAskCoach(academyStudioCoachPrompt(coachLevel, coachDraft, lesson.title))
    setCoachDraft('')
  }

  const renderPhase = () => {
    if (phase === 'review') return <div className="space-y-2">
      <Button size="xs" variant="ghost" data-academy-cumulative-toggle aria-pressed={cumulativeMode} onClick={() => setCumulativeMode(value => !value)}>{t(cumulativeMode ? 'ui.academy.mastery.backToDue' : 'ui.academy.mastery.cumulative')}</Button>
      <AcademyReviewSession projectId={project.id} active={active} disabled={disabled} onCompleted={() => markDone('review')} onOpenLesson={onOpenLesson} mode={cumulativeMode ? 'cumulative' : 'due'} />
    </div>
    if (phase === 'struggle') return <AcademyStruggle projectId={project.id} lesson={lesson} active={active} disabled={disabled} onPhase={onStrugglePhase} />
    if (phase === 'world') return <AcademyWorld projectId={project.id} lesson={lesson} active={active} disabled={disabled} onCompleted={() => markDone('world')} />
    if (phase === 'steps') return <AcademySteps projectId={project.id} lesson={lesson} active={active} disabled={disabled} onCompleted={() => markDone('steps')} onCalibration={code => {if (code === 'blindspot') setNudge('blindspot')}} />
    if (phase === 'reading') {
      if (lesson.struggle && !struggleRevealed) return <p className="text-sm leading-reading text-muted">{t('ui.academy.struggle.gated')}</p>
      return <div className="space-y-3">
        {lesson.stage_goal ? <p className="text-xs text-muted">{t('ui.academy.studio.reading.stage')}: {lesson.stage_goal}</p> : null}
        {lesson.prerequisites.length ? <div className="flex flex-wrap items-center gap-1">
          <span className="text-xs text-muted">{t('ui.academy.prerequisiteHint')}</span>
          {lesson.prerequisites.map(id => {const pre = course.lessons.find(item => item.id === id); return pre ? <Button key={id} size="xs" variant="ghost" onClick={() => onOpenLesson(id)}>{t('ui.academy.lessonTitle', {order: pre.order, title: pre.title})}</Button> : null})}
        </div> : null}
        <div className="min-w-0 text-base leading-reading"><Markdown workspaceLinks={false}>{lesson.reading}</Markdown></div>
        {!done.has('reading') ? <Button size="sm" variant="subtle" onClick={() => markDone('reading')}>{t('ui.academy.studio.readDone')}</Button> : null}
      </div>
    }
    if (phase === 'teach') return <AcademyTeach embedded projectId={project.id} lesson={lesson} fingerprint={project.fingerprint} turns={tutorTurns} disabled={disabled} active={active} onRefresh={onRefresh} onCompleted={() => markDone('teach')} />
    if (phase === 'checks') return <AcademyKnowledgeChecks embedded checks={checks} disabled={disabled} asking={coachRunning} onChecked={onCheckAnswer} />
    if (phase === 'note') return <label className="block space-y-2 text-sm">
      <span className="text-muted">{t('ui.academy.studio.noteHint')}</span>
      <textarea className="ui-field min-h-24 w-full resize-y leading-reading" value={note} maxLength={4000} aria-label={t('ui.academy.note')} placeholder={t('ui.academy.studyNoteHint')}
        onChange={event => onNote(event.target.value)} />
    </label>
    if (studyCompleted) return <div role="status" className="space-y-3">
      <p className="flex items-center gap-2 text-base font-medium text-ok"><Check className="h-5 w-5" aria-hidden />{t('ui.academy.studio.finish.done')}</p>
      <p className="text-sm text-muted">{t('ui.academy.studio.session.summary', {done: done.size, total: phases.length})}</p>
      {masteryView ? <AcademyReport mastery={masteryView} /> : null}
      <div className="flex flex-wrap gap-2">
        {nextLesson ? <Button size="sm" variant="primary" onClick={() => onOpenLesson(nextLesson.id)}>{t('ui.academy.studio.finish.next', {title: nextLesson.title})}</Button>
          : <p className="text-sm text-muted">{t('ui.academy.studio.finish.courseDone')}</p>}
        <Button size="sm" variant="ghost" onClick={onClose}>{t('ui.academy.studio.finish.back')}</Button>
      </div>
    </div>
    return <div className="space-y-3">
      <ul className="space-y-1 text-sm">
        <li className={checksDone ? 'text-ok' : 'text-muted'}>{t(checksDone ? 'ui.academy.studio.finish.checksDone' : 'ui.academy.studio.finish.checksTodo')}</li>
        <li className={note.trim() ? 'text-ok' : 'text-muted'}>{t(note.trim() ? 'ui.academy.studio.finish.noteDone' : 'ui.academy.studio.finish.noteTodo')}</li>
      </ul>
      <p className="text-sm leading-reading text-muted">{t('ui.academy.studio.finish.hint')}</p>
      <Button size="md" variant="primary" disabled={disabled || !studyReady} icon={<Check className="h-3.5 w-3.5" />} onClick={onComplete}>{t('ui.academy.completeStudy')}</Button>
    </div>
  }

  const currentPhase = phases.find(item => item.id === phase)
  return <div ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-label={t('ui.academy.studio.dialogLabel')} data-academy-studio
    className="fixed inset-0 z-50 flex flex-col bg-canvas focus-visible:outline focus-visible:outline-brand"
    data-reduced-motion={prefs.reducedMotion ? 'true' : undefined}
    style={prefs.large ? {zoom: 1.15} : undefined}
    onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
    onKeyDown={event => {
      if (composing.current || event.nativeEvent.isComposing) return
      if (event.key === '.' && (event.ctrlKey || event.metaKey)) {event.preventDefault(); updatePrefs({...prefs, zen: !prefs.zen}); return}
      const target = event.target as HTMLElement
      if (target.closest('input, textarea, select')) return
      if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
      const index = phases.findIndex(item => item.id === phase)
      const next = phases[index + (event.key === 'ArrowRight' ? 1 : -1)]
      if (next) {event.preventDefault(); manual.current = true; setPhase(next.id)}
    }}>
    {celebrate ? <div data-confetti aria-hidden className="pointer-events-none fixed inset-0 z-50 overflow-hidden">
      {Array.from({length: 24}, (_, i) => <span key={i} className="academy-confetti-piece" style={{left: (i * 4.16) + '%', animationDelay: (i % 6) * 0.08 + 's', background: ['#22c55e', '#3b82f6', '#f59e0b', '#ec4899'][i % 4]}} />)}
    </div> : null}
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
      <div className="flex min-w-0 items-center gap-2">
        <Sparkles className="h-4 w-4 shrink-0 text-brand" aria-hidden />
        <h2 className="truncate text-base font-semibold">{course.title}</h2>
        <span className="shrink-0 text-xs text-muted">{t('ui.academy.progress', {done: completedCount, total: course.lessons.length})}</span>
      </div>
      <Button size="xs" variant="ghost" data-academy-pref-zen aria-pressed={prefs.zen} title={t('ui.academy.studio.prefs.zenTitle')} onClick={() => updatePrefs({...prefs, zen: !prefs.zen})}>{t('ui.academy.studio.prefs.zen')}</Button>
      {!prefs.zen ? <Button size="xs" variant="ghost" data-academy-pref-font aria-pressed={prefs.large} onClick={() => updatePrefs({...prefs, large: !prefs.large})}>{t(prefs.large ? 'ui.academy.studio.prefs.fontNormal' : 'ui.academy.studio.prefs.fontLarge')}</Button> : null}
      {!prefs.zen ? <Button size="xs" variant="ghost" data-academy-pref-motion aria-pressed={prefs.reducedMotion} onClick={() => updatePrefs({...prefs, reducedMotion: !prefs.reducedMotion})}>{t('ui.academy.studio.prefs.motion')}</Button> : null}
      <Button size="xs" variant="ghost" data-academy-studio-exit icon={<X className="h-3.5 w-3.5" />} onClick={onClose}>{t('ui.academy.studio.exit')}</Button>
    </header>
    {!prefs.zen ? <nav data-studio-journey aria-label={t('ui.academy.studio.journeyLabel')} className="shrink-0 overflow-x-auto border-b border-line px-4 py-2">
      <ol className="flex min-w-max items-center gap-1">
        {journey.map((node, index) => {
          const skill = masteryView?.skills.find(row => row.lesson_id === node.id && row.evidence_count > 0)
          return <li key={node.id} className="flex items-center gap-1">
          {index > 0 ? <span aria-hidden className="h-0.5 w-4 rounded bg-line" /> : null}
          <button type="button" disabled={node.id === lesson.id || disabled} aria-current={node.state === 'active' ? 'step' : undefined}
            onClick={() => onOpenLesson(node.id)}
            className={cn('flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm', node.state === 'active' ? 'border-brand bg-brand/10 font-medium' : node.state === 'completed' ? 'border-ok/40 text-ok' : 'border-line text-muted')}>
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-current text-xs">{node.state === 'completed' ? <Check className="h-3 w-3" aria-hidden /> : node.order}</span>
            <span className="max-w-40 truncate">{node.title}</span>
            {skill ? <svg data-journey-memory width="18" height="18" viewBox="0 0 18 18" role="img" aria-label={t('ui.academy.studio.journey.memory', {percent: Math.round(skill.retrievability * 100)})} className="shrink-0">
              <circle cx="9" cy="9" r="7" fill="none" strokeWidth="2.5" className="stroke-line" />
              <circle cx="9" cy="9" r="7" fill="none" strokeWidth="2.5" strokeLinecap="round" className={skill.retrievability >= 0.8 ? 'stroke-ok' : skill.retrievability >= 0.5 ? 'stroke-warn' : 'stroke-danger'}
                strokeDasharray={2 * Math.PI * 7} strokeDashoffset={2 * Math.PI * 7 * (1 - Math.max(0, Math.min(1, skill.retrievability)))} transform="rotate(-90 9 9)" />
            </svg> : node.mastery ? <span role="img" aria-label={t('ui.academy.studio.journey.mastery.' + node.mastery)} className={'h-2 w-2 shrink-0 rounded-full ' + (node.mastery === 'durable' ? 'bg-ok' : 'bg-warn')} /> : null}
            {node.recommended ? <span className="rounded-full bg-brand/15 px-1.5 text-brand">{t('ui.academy.studio.journey.recommended')}</span> : null}
          </button>
        </li>})}
      </ol>
    </nav> : null}
    {!prefs.zen ? <div data-studio-session className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line px-4 py-2 text-xs text-muted">
      <span className="font-medium">{t('ui.academy.studio.session.label')}</span>
      {dueCount > 0 ? <span className="rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 text-warn">{t('ui.academy.studio.session.plan.review', {count: dueCount})}</span> : null}
      <span className="rounded-full border border-line px-2 py-0.5">{t('ui.academy.studio.session.plan.new', {title: lesson.title})}</span>
      <span className="rounded-full border border-line px-2 py-0.5">{t('ui.academy.studio.session.plan.reflect')}</span>
      {recommended && recommended.id !== lesson.id ? <span className="rounded-full border border-brand/40 bg-brand/10 px-2 py-0.5 text-brand">{t('ui.academy.studio.session.plan.recommended', {title: recommended.title})}</span> : null}
      <span className="ml-auto">{t('ui.academy.studio.session.estimate', {minutes: lesson.duration_minutes ?? 15})}</span>
      <svg data-progress-ring width="26" height="26" viewBox="0 0 26 26" role="img" aria-label={t('ui.academy.studio.session.progress', {done: done.size, total: phases.length})}>
        <circle cx="13" cy="13" r="11" fill="none" strokeWidth="3" className="stroke-line" />
        <circle cx="13" cy="13" r="11" fill="none" strokeWidth="3" strokeLinecap="round" className="stroke-brand transition-all duration-500"
          strokeDasharray={2 * Math.PI * 11} strokeDashoffset={2 * Math.PI * 11 * (1 - (phases.length ? done.size / phases.length : 0))} transform="rotate(-90 13 13)" />
      </svg>
      <span data-studio-elapsed>{t('ui.academy.studio.session.elapsed', {minutes: Math.floor(elapsed / 60)})}</span>
      {masteryView && (masteryView.streak_days ?? 0) > 0 ? <span className="flex items-center gap-1 rounded-full border border-ok/40 bg-ok/10 px-2 py-0.5 text-ok"><Flame className="h-3 w-3" aria-hidden />{t('ui.academy.studio.session.streak', {days: masteryView.streak_days})}</span> : null}
      <AcademyDailyGoal activity={masteryView?.activity} compact />
      {elapsed >= (lesson.duration_minutes ?? 15) * 60 ? <span className="rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 text-warn">{t('ui.academy.studio.session.rest')}</span> : null}
      <span aria-label={t('ui.academy.studio.session.progress', {done: done.size, total: phases.length})}>{t('ui.academy.studio.session.progress', {done: done.size, total: phases.length})}</span>
    </div> : null}
    <main className="flex-1 overflow-y-auto" onTouchStart={onSwipeStart} onTouchEnd={onSwipeEnd}>
      <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-5">
        <div>
          <p className="text-xs text-muted">{t('ui.academy.lessonTitle', {order: lesson.order, title: lesson.title})}</p>
          <h3 className="mt-1 text-xl font-semibold leading-snug">{lesson.goal}</h3>
        </div>
        <ol data-studio-phases className={prefs.zen ? 'flex flex-wrap items-center gap-1.5' : 'flex flex-wrap gap-1'} aria-label={t('ui.academy.studio.phaseLabel')}>
          {phases.map(item => <li key={item.id}>
            {prefs.zen ? <button type="button" data-phase-dot={item.id} aria-current={item.id === phase ? 'step' : undefined} aria-label={t(phaseLabelKey[item.id])} title={t(phaseLabelKey[item.id])}
              onClick={() => {manual.current = true; setPhase(item.id)}}
              className={cn('h-3.5 w-3.5 rounded-full border transition-colors', item.id === phase ? 'border-brand bg-brand' : done.has(item.id) ? 'border-ok/60 bg-ok/50' : 'border-line bg-line')} />
            : <button type="button" aria-current={item.id === phase ? 'step' : undefined} onClick={() => {manual.current = true; setPhase(item.id)}}
              className={cn('flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs', item.id === phase ? 'border-brand bg-brand/10 font-medium text-brand' : done.has(item.id) ? 'border-ok/40 text-ok' : 'border-line text-muted')}>
              {done.has(item.id) ? <Check className="h-3 w-3" aria-hidden /> : null}{t(phaseLabelKey[item.id])}
              {item.id === 'review' && dueCount > 0 ? <span className="rounded-full bg-warn/15 px-1.5 text-warn">{dueCount}</span> : null}
              {item.optional ? <span className="text-muted">· {t('ui.academy.studio.optional')}</span> : null}
            </button>}
          </li>)}
        </ol>
        <section data-studio-phase={phase} aria-label={t(phaseLabelKey[phase])} className="space-y-3 rounded-2xl border border-line p-4 sm:p-6">
          {renderPhase()}
          {currentPhase?.optional && !done.has(phase) && phase !== 'finish' ? <Button size="xs" variant="ghost" onClick={() => markDone(phase)}>{t('ui.academy.studio.skip')}</Button> : null}
        </section>
        {/* 教练对话进入舞台：与练习同屏的流式苏格拉底陪伴，而非页脚一次性问答。 */}
        <section data-studio-coach-stream aria-label={t('ui.academy.studio.coach.title')} className="space-y-2">
          <ol ref={threadRef} data-studio-coach-thread className="max-h-72 space-y-3 overflow-y-auto rounded-2xl border border-line bg-canvas p-4 text-sm">
            {coachTurns.length ? coachTurns.slice(-6).map(turn => <li key={turn.id} className="space-y-2">
              <div className="ml-auto max-w-xl rounded-2xl rounded-br-sm bg-brand/10 p-3">
                <p className="text-xs font-medium text-muted">{t('ui.academy.studio.coach.you')}</p>
                <p className="whitespace-pre-wrap break-words leading-reading">{turn.prompt.includes('\n') ? turn.prompt.slice(turn.prompt.indexOf('\n') + 1) : t('ui.academy.studio.coach.generalAsk')}</p>
              </div>
              <div className="max-w-2xl rounded-2xl rounded-bl-sm border border-line bg-raised p-3">
                <p className="flex items-center gap-1 text-xs font-medium text-brand"><Sparkles className="h-3 w-3" aria-hidden />{t('ui.academy.studio.coach.title')}</p>
                {turn.status === 'running' ? (turn.partial_response && !turn.stopping
                  ? <p aria-label={t('ui.academy.tutor.partial')} aria-busy="true" className="whitespace-pre-wrap break-words leading-reading">{turn.partial_response}<span className="streaming-caret" aria-hidden /></p>
                  : <p role="status" className="flex gap-2 text-muted"><Spinner />{t('ui.academy.loading')}</p>)
                  : turn.status === 'completed' ? <div className="min-w-0 leading-reading"><Markdown workspaceLinks={false}>{turn.response}</Markdown></div>
                  : <p className="text-warn">{t('ui.academy.studio.coach.failed')}</p>}
              </div>
            </li>) : <li className="max-w-2xl rounded-2xl rounded-bl-sm border border-line bg-raised p-3">
              <p className="flex items-center gap-1 text-xs font-medium text-brand"><Sparkles className="h-3 w-3" aria-hidden />{t('ui.academy.studio.coach.title')}</p>
              <p className="leading-reading text-muted">{t('ui.academy.studio.coach.empty')}</p>
            </li>}
          </ol>
          <p className="sr-only" role="status">{latestCoachTurn?.status === 'completed' ? t('ui.academy.studio.coach.answered') : ''}</p>
          {awaitingReply ? <Button size="xs" variant="subtle" data-coach-awaiting onClick={() => composerRef.current?.focus()}>{t('ui.academy.studio.coach.awaiting')}</Button> : null}
        </section>
      </div>
    </main>
    <footer data-studio-coach className="shrink-0 border-t border-line px-4 py-3">
      <div className="mx-auto w-full max-w-3xl space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="flex items-center gap-1 text-sm font-medium"><Sparkles className="h-3.5 w-3.5 text-brand" aria-hidden />{t('ui.academy.studio.coach.title')}</p>
          <div role="group" aria-label={t('ui.academy.studio.coach.levelLabel')} className="flex rounded-lg border border-line p-0.5 text-xs">
            {([0, 1, 2] as const).map(level => <button key={level} type="button" data-coach-level={level} aria-pressed={coachLevel === level} onClick={() => setCoachLevel(level)}
              className={cn('rounded-md px-2 py-1', coachLevel === level ? 'bg-brand/15 font-medium text-brand' : 'text-muted')}>{t('ui.academy.studio.coach.level.' + coachLevelNames[level])}</button>)}
          </div>
        </div>
        <p className="text-xs text-muted">{t('ui.academy.studio.coach.levelHint.' + coachLevelNames[coachLevel])}</p>
        {nudge ? <div role="status" data-studio-nudge={nudge} className="flex flex-wrap items-center gap-2 rounded-lg border border-brand/40 bg-brand/5 p-2 text-sm">
          <p className="min-w-0 flex-1 leading-reading">{t('ui.academy.studio.nudge.' + nudge)}</p>
          <Button size="xs" variant="subtle" disabled={disabled || coachRunning} onClick={() => {setCoachLevel(1); onAskCoach(academyStudioCoachPrompt(1, t('ui.academy.studio.nudge.prompt.' + nudge), lesson.title)); setNudge(null)}}>{t('ui.academy.studio.nudge.action')}</Button>
          <Button size="xs" variant="ghost" aria-label={t('ui.academy.studio.nudge.dismiss')} onClick={() => setNudge(null)}>{t('ui.academy.studio.nudge.dismiss')}</Button>
        </div> : null}
        <div className="flex flex-wrap gap-1" data-coach-suggestions>
          {coachSuggestions.map(suggestion => <button key={suggestion.key} type="button" data-coach-suggest={suggestion.key} disabled={disabled || coachRunning}
            onClick={() => {setCoachLevel(suggestion.level); onAskCoach(academyStudioCoachPrompt(suggestion.level, t('ui.academy.studio.coach.suggest.' + suggestion.key), lesson.title))}}
            className="rounded-full border border-line px-2.5 py-1 text-xs text-muted hover:border-brand hover:text-brand">{t('ui.academy.studio.coach.suggest.' + suggestion.key)}</button>)}
        </div>
        <form className="flex items-end gap-2"
          onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}
          onSubmit={event => {event.preventDefault(); sendCoach()}}>
          <textarea ref={composerRef} rows={1} className="ui-field max-h-24 min-h-10 flex-1 resize-y leading-reading" value={coachDraft} placeholder={t('ui.academy.studio.coach.placeholder')} aria-label={t('ui.academy.studio.coach.placeholder')}
            onChange={event => setCoachDraft(event.target.value)}
            onKeyDown={event => {if (event.key === 'Enter' && !event.shiftKey && !event.altKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {event.preventDefault(); sendCoach()}}} />
          <AcademyVoiceButton active={active && !disabled} disabled={disabled || coachRunning} onText={value => setCoachDraft(previous => previous ? previous + ' ' + value : value)} />
          <Button type="submit" size="sm" disabled={disabled || coachRunning} icon={<Send className="h-3.5 w-3.5" />}>{t('ui.academy.studio.coach.send')}</Button>
        </form>
      </div>
    </footer>
  </div>
}
