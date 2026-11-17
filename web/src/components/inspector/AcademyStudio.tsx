import {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import {Check, Send, Sparkles, X} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import type {AcademyCourse, AcademyLesson, AcademyProject, AcademyTutorTurn} from '@/types'
import {academyApi} from '@/lib/academy'
import {academyStudioChecksDone, academyStudioCoachPrompt, academyStudioCoachTurns, academyStudioJourney, academyStudioNextPhase, academyStudioPhases, type AcademyStudioCoachLevel, type AcademyStudioPhaseId} from '@/lib/academyStudio'
import {cn} from '@/lib/utils'
import {Markdown} from '@/components/markdown/Markdown'
import {Button, Spinner} from '@/components/ui/primitives'
import {AcademyStruggle} from './AcademyStruggle'
import {AcademyWorld} from './AcademyWorld'
import {AcademySteps} from './AcademySteps'
import {AcademyTeach} from './AcademyTeach'
import {AcademyKnowledgeChecks} from './AcademyKnowledgeChecks'
import {AcademyReviewSession} from './AcademyMastery'

const phaseLabelKey: Record<AcademyStudioPhaseId, string> = {
  review: 'ui.academy.studio.phase.review', struggle: 'ui.academy.studio.phase.struggle', world: 'ui.academy.studio.phase.world', steps: 'ui.academy.studio.phase.steps',
  reading: 'ui.academy.studio.phase.reading', teach: 'ui.academy.studio.phase.teach', checks: 'ui.academy.quickCheck',
  note: 'ui.academy.studio.phase.note', finish: 'ui.academy.studio.phase.finish',
}
const coachLevelNames = ['try', 'hint', 'explain'] as const

/** 专注学习舞台：全屏单焦点学习界面。旅程路径 + 一次只呈现一个学习环节 + 底部 AI 教练（自主性三档）。 */
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
  const [phase, setPhase] = useState<AcademyStudioPhaseId>(() => studyCompleted || studyReady ? 'finish' : phases[0].id)
  const [coachLevel, setCoachLevel] = useState<AcademyStudioCoachLevel>(0)
  const [nudge, setNudge] = useState<'struggle' | 'blindspot' | null>(() => struggleRevealed ? 'struggle' : null)
  const [coachDraft, setCoachDraft] = useState('')
  const composing = useRef(false)
  const phaseRef = useRef(phase); phaseRef.current = phase
  const doneRef = useRef(done); doneRef.current = done
  const closeRef = useRef(onClose); closeRef.current = onClose
  const dialog = useRef<HTMLDivElement | null>(null)
  const journey = academyStudioJourney(course.lessons, project.progress, lesson.id)
  const coachTurns = academyStudioCoachTurns(tutorTurns, project.id, lesson.id)
  const latestCoach = coachTurns.at(-1)
  const coachRunning = coachTurns.some(turn => turn.status === 'running')
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

  /** 到期独立重测优先于新内容：掌握学习先补旧缺口再前进。 */
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    void (async () => {
      const mastery = await academyApi.mastery(project.id, controller.signal)
      if (controller.signal.aborted) return
      if (mastery.ok) setDueCount(mastery.data.mastery.due_count)
    })()
    return () => controller.abort()
  }, [active, project.id])
  useEffect(() => {if (dueCount > 0 && !manual.current && !doneRef.current.has('review')) setPhase('review')}, [dueCount])

  useEffect(() => {
    dialog.current?.focus()
    const onKey = (event: KeyboardEvent) => {if (event.key === 'Escape' && !composing.current) closeRef.current()}
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const sendCoach = () => {
    if (composing.current || disabled || coachRunning) return
    onAskCoach(academyStudioCoachPrompt(coachLevel, coachDraft, lesson.title))
    setCoachDraft('')
  }

  const renderPhase = () => {
    if (phase === 'review') return <AcademyReviewSession projectId={project.id} active={active} disabled={disabled} onCompleted={() => markDone('review')} />
    if (phase === 'struggle') return <AcademyStruggle projectId={project.id} lesson={lesson} active={active} disabled={disabled} onPhase={onStrugglePhase} />
    if (phase === 'world') return <AcademyWorld projectId={project.id} lesson={lesson} active={active} disabled={disabled} onCompleted={() => markDone('world')} />
    if (phase === 'steps') return <AcademySteps projectId={project.id} lesson={lesson} active={active} disabled={disabled} onCompleted={() => markDone('steps')} onCalibration={code => {if (code === 'blindspot') setNudge('blindspot')}} />
    if (phase === 'reading') {
      if (lesson.struggle && !struggleRevealed) return <p className="text-sm leading-reading text-muted">{t('ui.academy.struggle.gated')}</p>
      return <div className="space-y-3">
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
    onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}>
    <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-3">
      <div className="flex min-w-0 items-center gap-2">
        <Sparkles className="h-4 w-4 shrink-0 text-brand" aria-hidden />
        <h2 className="truncate text-base font-semibold">{course.title}</h2>
        <span className="shrink-0 text-xs text-muted">{t('ui.academy.progress', {done: completedCount, total: course.lessons.length})}</span>
      </div>
      <Button size="xs" variant="ghost" data-academy-studio-exit icon={<X className="h-3.5 w-3.5" />} onClick={onClose}>{t('ui.academy.studio.exit')}</Button>
    </header>
    <nav data-studio-journey aria-label={t('ui.academy.studio.journeyLabel')} className="shrink-0 overflow-x-auto border-b border-line px-4 py-2">
      <ol className="flex min-w-max items-center gap-1">
        {journey.map((node, index) => <li key={node.id} className="flex items-center gap-1">
          {index > 0 ? <span aria-hidden className="h-0.5 w-4 rounded bg-line" /> : null}
          <button type="button" disabled={node.id === lesson.id || disabled} aria-current={node.state === 'active' ? 'step' : undefined}
            onClick={() => onOpenLesson(node.id)}
            className={cn('flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm', node.state === 'active' ? 'border-brand bg-brand/10 font-medium' : node.state === 'completed' ? 'border-ok/40 text-ok' : 'border-line text-muted')}>
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-current text-xs">{node.state === 'completed' ? <Check className="h-3 w-3" aria-hidden /> : node.order}</span>
            <span className="max-w-40 truncate">{node.title}</span>
          </button>
        </li>)}
      </ol>
    </nav>
    <main className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-5">
        <div>
          <p className="text-xs text-muted">{t('ui.academy.lessonTitle', {order: lesson.order, title: lesson.title})}</p>
          <h3 className="mt-1 text-xl font-semibold leading-snug">{lesson.goal}</h3>
        </div>
        <ol className="flex flex-wrap gap-1" aria-label={t('ui.academy.studio.phaseLabel')}>
          {phases.map(item => <li key={item.id}>
            <button type="button" aria-current={item.id === phase ? 'step' : undefined} onClick={() => {manual.current = true; setPhase(item.id)}}
              className={cn('flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs', item.id === phase ? 'border-brand bg-brand/10 font-medium text-brand' : done.has(item.id) ? 'border-ok/40 text-ok' : 'border-line text-muted')}>
              {done.has(item.id) ? <Check className="h-3 w-3" aria-hidden /> : null}{t(phaseLabelKey[item.id])}
              {item.id === 'review' && dueCount > 0 ? <span className="rounded-full bg-warn/15 px-1.5 text-warn">{dueCount}</span> : null}
              {item.optional ? <span className="text-muted">· {t('ui.academy.studio.optional')}</span> : null}
            </button>
          </li>)}
        </ol>
        <section data-studio-phase={phase} aria-label={t(phaseLabelKey[phase])} className="space-y-3 rounded-2xl border border-line p-4 sm:p-6">
          {renderPhase()}
          {currentPhase?.optional && !done.has(phase) && phase !== 'finish' ? <Button size="xs" variant="ghost" onClick={() => markDone(phase)}>{t('ui.academy.studio.skip')}</Button> : null}
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
        {latestCoach ? <div role="status" className="max-h-40 overflow-y-auto rounded-lg bg-canvas p-3 text-sm">
          {latestCoach.status === 'running' ? <p className="flex gap-2 text-muted"><Spinner />{t('ui.academy.loading')}</p>
            : latestCoach.status === 'completed' ? <div className="min-w-0 leading-reading"><Markdown workspaceLinks={false}>{latestCoach.response}</Markdown></div>
            : <p className="text-warn">{t('ui.academy.studio.coach.failed')}</p>}
        </div> : <p className="text-xs text-muted">{t('ui.academy.studio.coach.empty')}</p>}
        <form className="flex items-end gap-2"
          onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}
          onSubmit={event => {event.preventDefault(); sendCoach()}}>
          <textarea rows={1} className="ui-field max-h-24 min-h-10 flex-1 resize-y leading-reading" value={coachDraft} placeholder={t('ui.academy.studio.coach.placeholder')} aria-label={t('ui.academy.studio.coach.placeholder')}
            onChange={event => setCoachDraft(event.target.value)}
            onKeyDown={event => {if (event.key === 'Enter' && !event.shiftKey && !event.altKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {event.preventDefault(); sendCoach()}}} />
          <Button type="submit" size="sm" disabled={disabled || coachRunning} icon={<Send className="h-3.5 w-3.5" />}>{t('ui.academy.studio.coach.send')}</Button>
        </form>
      </div>
    </footer>
  </div>
}
