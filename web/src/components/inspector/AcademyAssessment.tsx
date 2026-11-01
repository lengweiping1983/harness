import {useId} from 'react'
import {Star} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import type {AcademyLesson, AcademySubmission} from '@/types'
import {academyDimensions, academyRadarPoint} from '@/lib/academy'
import {Section, Spinner} from '@/components/ui/primitives'

const assessmentErrors: Record<string, string> = {
  'assessment-unavailable': 'ui.academy.error.assessment-unavailable',
  'assessment-failed': 'ui.academy.error.assessment-failed',
  'assessment-invalid': 'ui.academy.error.assessment-invalid',
  'assessment-timeout': 'ui.academy.error.assessment-timeout',
  'assessment-interrupted': 'ui.academy.error.assessment-interrupted',
  'assessment-busy': 'ui.academy.error.assessment-busy',
  'submission-too-large': 'ui.academy.error.submission-too-large',
}

export function AcademyRubric({lesson}: {lesson?: Pick<AcademyLesson, 'order' | 'title' | 'rubric'>} = {}) {
  useLocale()
  return <Section title={t('ui.academy.rubricTitle')}>
    <p className="text-sm leading-reading text-muted">{t('ui.academy.rubricIntro')}</p>
    <ol className="grid gap-x-4 gap-y-1 text-xs text-muted sm:grid-cols-2" start={0}>
      {[0, 1, 2, 3, 4, 5].map(score => <li key={score}>{t('ui.academy.rubric.' + score)}</li>)}
    </ol>
    {lesson?.rubric ? <details className="text-xs text-muted"><summary className="cursor-pointer rounded-lg py-1 focus-visible:outline focus-visible:outline-brand">{t('ui.academy.lessonTitle', {order: lesson.order, title: lesson.title})}</summary><p className="mt-2 text-sm leading-reading">{lesson.rubric}</p></details> : null}
  </Section>
}

/** 评级图只绘制已观察到的数值；空值不落在代表零分的中心。 */
export function AcademyAssessment({submission}: {submission: AcademySubmission | null}) {
  useLocale()
  const titleId = useId()
  const descriptionId = useId()
  if (!submission) return <p className="text-sm leading-reading text-muted">{t('ui.academy.assessmentEmpty')}</p>
  const assessment = submission.assessment
  const dimensions = academyDimensions(assessment?.dimensions ?? [])
  const observed = dimensions.map(({dimension}, index) => dimension && dimension.score !== null ? {index, score: dimension.score, point: academyRadarPoint(index, dimension.score)} : null)
  return <div className="space-y-4" data-submission-id={submission.id}>
    <p className="text-xs leading-reading text-muted">{t('ui.academy.assessmentSnapshot')}</p>
    <p className="text-xs text-faint">{t('ui.academy.assessmentLanguage', {language: submission.language, model: submission.model})}</p>
    {submission.note ? <details className="text-sm text-muted"><summary className="cursor-pointer rounded-lg py-1 focus-visible:outline focus-visible:outline-brand">{t('ui.academy.note')}</summary><p className="mt-2 whitespace-pre-wrap break-words leading-reading">{submission.note}</p></details> : null}
    {submission.status === 'evaluating' ? <p role="status" className="flex items-center gap-2 text-sm text-brand"><Spinner />{t('ui.academy.assessmentPending')}</p> : null}
    {submission.status === 'failed' || submission.status === 'unknown' ? <div role="status" className="space-y-1 text-sm text-warn"><p>{t(submission.status === 'unknown' ? 'ui.academy.assessmentUnknown' : 'ui.academy.assessmentFailed')}</p>{submission.error_code ? <>{Object.hasOwn(assessmentErrors, submission.error_code) ? <p className="leading-reading">{t(assessmentErrors[submission.error_code])}</p> : null}<p className="break-all font-mono text-xs">{submission.error_code}</p></> : null}</div> : null}
    {assessment ? <>
      <p className="text-base leading-reading">{assessment.summary}</p>
      <svg viewBox="-30 0 420 300" role="img" aria-labelledby={`${titleId} ${descriptionId}`} className="mx-auto w-full max-w-md text-muted">
        <title id={titleId}>{t('ui.academy.radarLabel')}</title>
        <desc id={descriptionId}>{t('ui.academy.radarDescription')} {dimensions.map(({id, dimension}) => `${t('ui.academy.dimension.' + id)}: ${dimension?.score === null || dimension?.score === undefined ? t('ui.academy.notObserved') : t('ui.academy.score', {score: dimension.score})}`).join('; ')}</desc>
        {[1, 2, 3, 4, 5].map(score => <polygon key={score} points={dimensions.map((_, index) => {const point = academyRadarPoint(index, score); return `${point.x},${point.y}`}).join(' ')} fill="none" stroke="currentColor" strokeOpacity="0.18" />)}
        {dimensions.map(({id}, index) => {
          const end = academyRadarPoint(index, 5)
          const label = academyRadarPoint(index, 5, 124)
          return <g key={id}><line x1="180" y1="150" x2={end.x} y2={end.y} stroke="currentColor" strokeOpacity="0.25" /><text x={label.x} y={label.y} textAnchor="middle" dominantBaseline="middle" fill="currentColor" fontSize="12">{t('ui.academy.dimension.' + id)}</text></g>
        })}
        {observed.every(point => point !== null) ? <polygon points={observed.map(value => value && `${value.point.x},${value.point.y}`).join(' ')} className="fill-brand/15 stroke-brand" strokeWidth="2" /> : observed.map((value, index) => {
          const next = observed[(index + 1) % observed.length]
          return value && next ? <line key={index} x1={value.point.x} y1={value.point.y} x2={next.point.x} y2={next.point.y} className="stroke-brand" strokeWidth="2" /> : null
        })}
        {observed.map(value => value ? <circle key={value.index} data-dimension={dimensions[value.index].id} cx={value.point.x} cy={value.point.y} r="4" className="fill-brand" /> : null)}
      </svg>
      <div className="space-y-3">{dimensions.map(({id, dimension}) => <div key={id} className="space-y-2 rounded-xl border border-line p-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="ui-card-title">{t('ui.academy.dimension.' + id)}</h4>{dimension?.score !== null && dimension?.score !== undefined ? <span role="img" aria-label={t('ui.academy.score', {score: dimension.score})} className="inline-flex items-center gap-1 text-brand"><span aria-hidden="true" className="inline-flex">{[0, 1, 2, 3, 4].map(index => <Star key={index} className="h-3.5 w-3.5" fill={index < dimension.score! ? 'currentColor' : 'none'} />)}</span><span className="text-xs">{dimension.score}/5</span></span> : <span className="text-xs text-muted">{t('ui.academy.notObserved')}</span>}</div>
        {dimension ? <><p className="text-sm leading-reading">{dimension.reason}</p>{dimension.evidence.length ? <details className="text-xs text-muted"><summary className="cursor-pointer rounded-lg py-1 focus-visible:outline focus-visible:outline-brand">{t('ui.academy.evidence')}</summary><ul className="space-y-2 py-2">{dimension.evidence.map((evidence, index) => <li key={index}><p className="break-all font-mono text-xs">{evidence.source}</p><blockquote className="mt-1 whitespace-pre-wrap break-words border-l-2 border-line pl-3 leading-reading">{evidence.quote}</blockquote></li>)}</ul></details> : null}<p className="text-sm leading-reading"><span className="font-medium text-brand">{t('ui.academy.nextStep')}</span> {dimension.next_step}</p></> : null}
      </div>)}</div>
      {assessment.next_steps.length ? <Section title={t('ui.academy.nextStep')}><ul className="list-disc space-y-1 pl-5 text-sm leading-reading">{assessment.next_steps.map((step, index) => <li key={index}>{step}</li>)}</ul></Section> : null}
    </> : null}
  </div>
}
