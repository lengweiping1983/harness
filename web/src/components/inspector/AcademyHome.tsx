import {BookOpen} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import type {AcademyCourseSummary, AcademyProject} from '@/types'
import {Badge, Button} from '@/components/ui/primitives'

/** 学习主页课程卡：学段徽标＋跨课程到期重测徽章＋开始/继续入口。 */
export function AcademyHomeCard({item, saved, dueCount, disabled, onStart, onResume}: {
  item: AcademyCourseSummary; saved?: AcademyProject; dueCount: number; disabled: boolean; onStart: () => void; onResume: () => void
}) {
  useLocale()
  return <li data-course-id={item.id} className="flex flex-col gap-2 rounded-2xl border border-line p-4">
    <div className="flex items-start justify-between gap-2"><h3 className="text-base font-semibold">{item.title}</h3>{item.band ? <Badge tone="brand">{t('ui.academy.home.band.' + item.band)}</Badge> : null}</div>
    {saved ? <p className="break-words text-xs text-muted">{t('ui.academy.resumeProjectName', {name: saved.name})}</p> : null}
    {saved && dueCount > 0 ? <p className="text-xs font-medium text-warn" data-home-due={dueCount}>{t('ui.academy.home.dueBadge', {count: dueCount})}</p> : null}
    <p className="text-sm leading-reading text-muted">{item.description}</p>
    <div className="mt-auto flex flex-wrap items-center justify-between gap-2"><span className="text-xs text-muted">{t('ui.academy.lessonCount', {count: item.lesson_count})}</span>
      <Button size="sm" variant="primary" disabled={disabled} aria-label={t(saved ? 'ui.academy.resumeCourse' : 'ui.academy.startCourse', {title: item.title})} icon={<BookOpen className="h-3.5 w-3.5" />} onClick={() => saved ? onResume() : onStart()}>{t(saved ? 'ui.academy.resumeCourseAction' : 'ui.academy.startDesignedCourse')}</Button></div>
  </li>
}
