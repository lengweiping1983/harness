import { Check, ChevronRight } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import type { AcademyCourse, AcademyProject } from '@/types'
import { academyResumeLesson, academyStages } from '@/lib/academy'
import { Button } from '@/components/ui/primitives'
import { cn } from '@/lib/utils'

export function AcademyLearningPath({course, project, lessonId, disabled, onSelect}: {
  course: AcademyCourse; project: AcademyProject | null; lessonId: string; disabled: boolean; onSelect: (id: string) => void
}) {
  useLocale()
  const stages = academyStages(course)
  const progress = project?.course_id === course.id ? project.progress : []
  const passed = new Set(progress.filter(row => row.state !== 'in_progress').map(row => row.lesson_id))
  const resume = project ? academyResumeLesson(course, project) : undefined
  return <div className="space-y-3">
    {resume && resume.id !== lessonId ? <Button size="sm" variant="subtle" className="h-auto min-h-8 whitespace-normal text-left" disabled={disabled} iconRight={<ChevronRight className="h-3.5 w-3.5 shrink-0" />} onClick={() => onSelect(resume.id)}>{t('ui.academy.resumeLesson', {order: resume.order, title: resume.title})}</Button> : null}
    <details className="rounded-xl border border-line p-3">
      <summary className="cursor-pointer rounded-lg text-sm focus-visible:outline focus-visible:outline-brand">{t('ui.academy.learningPath', {count: stages.length})}</summary>
      <p className="mt-2 text-xs leading-reading text-muted">{t('ui.academy.learningPathHint')}</p>
      <ol className="mt-3 space-y-3" aria-label={t('ui.academy.pathStages')}>
        {stages.map((stage, index) => <li key={stage.id} className="space-y-2 border-l border-line pl-3">
          <h3 className="ui-card-title">{t('ui.academy.stageTitle', {number: index + 1, title: stage.title ?? t('ui.academy.lessons')})}</h3>
          {stage.goal ? <p className="text-sm leading-reading text-muted">{stage.goal}</p> : null}
          <p className="text-xs text-muted">{t('ui.academy.stageProgress', {done: stage.lessons.filter(lesson => passed.has(lesson.id)).length, total: stage.lessons.length})}</p>
          <ol className="space-y-1">{stage.lessons.map(lesson => <li key={lesson.id}>
            <button type="button" disabled={disabled} aria-current={lesson.id === lessonId ? 'step' : undefined} onClick={() => onSelect(lesson.id)} className={cn('flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm focus-visible:outline focus-visible:outline-brand disabled:opacity-50', lesson.id === lessonId ? 'bg-brand/[0.12] text-brand' : 'hover:bg-overlay')}>
              {passed.has(lesson.id) ? <Check aria-label={t(lesson.mode === 'python' ? 'ui.academy.checkPassed' : 'ui.academy.studyCompleted')} className="h-3.5 w-3.5 shrink-0 text-ok" /> : <span aria-hidden="true" className="h-3.5 w-3.5 shrink-0 rounded-full border border-line" />}
              <span className="min-w-0 break-words">{t('ui.academy.lessonTitle', {order: lesson.order, title: lesson.title})}</span>
            </button>
          </li>)}</ol>
        </li>)}
      </ol>
    </details>
  </div>
}
