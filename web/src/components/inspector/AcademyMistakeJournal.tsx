import {useState} from 'react'
import {t, useLocale} from '@/i18n'
import type {AcademyMistake} from '@/types'
import {cn} from '@/lib/utils'
import {Button} from '@/components/ui/primitives'

/** 错题本：跨课错误分析与重练入口。当前答错与已修复盲点来自持久作答记录；高确信错误＝盲点，答对后记为修复。 */
export function AcademyMistakeJournal({mistakes, onOpenLesson}: {mistakes?: ReadonlyArray<AcademyMistake>; onOpenLesson?: (lessonId: string) => void}) {
  const locale = useLocale()
  const [filter, setFilter] = useState<'all' | 'open'>('all')
  if (!mistakes?.length) return <p data-academy-journal-empty className="text-xs leading-reading text-muted">{t('ui.academy.journal.empty')}</p>
  const rows = mistakes.filter(item => filter === 'all' || !item.repaired)
  return <div data-academy-journal className="space-y-2">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p className="text-xs font-medium">{t('ui.academy.journal.title')}</p>
      <div role="group" aria-label={t('ui.academy.journal.title')} className="flex rounded-lg border border-line p-0.5 text-xs">
        {(['all', 'open'] as const).map(mode => <button key={mode} type="button" data-journal-filter={mode} aria-pressed={filter === mode} onClick={() => setFilter(mode)}
          className={cn('rounded-md px-2 py-1', filter === mode ? 'bg-brand/15 font-medium text-brand' : 'text-muted')}>{t('ui.academy.journal.filter.' + mode)}</button>)}
      </div>
    </div>
    <p className="text-xs leading-reading text-muted">{t('ui.academy.journal.intro')}</p>
    {rows.length ? <ol className="space-y-2">{rows.map(item => <li key={item.lesson_id + ':' + item.step_id} data-journal-item={item.step_id} className="space-y-1 rounded-xl border border-line p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 break-words font-medium">{item.lesson_title}</span>
        {item.repaired ? <span data-journal-badge="repaired" className="rounded-full bg-ok/10 px-2 py-0.5 text-xs text-ok">{t('ui.academy.journal.badge.repaired')}</span>
          : item.blindspot ? <span data-journal-badge="blindspot" className="rounded-full bg-warn/15 px-2 py-0.5 text-xs text-warn">{t('ui.academy.journal.badge.blindspot')}</span>
          : <span data-journal-badge="wrong" className="rounded-full bg-line px-2 py-0.5 text-xs text-muted">{t('ui.academy.journal.badge.wrong')}</span>}
        {item.confidence ? <span className="text-xs text-muted">{t('ui.academy.steps.confidence.' + item.confidence)}</span> : null}
        <span className="ml-auto text-xs text-muted">{new Date(item.at * 1000).toLocaleDateString(locale === 'zh-CN' ? 'zh-CN' : 'en')}</span>
      </div>
      <p className="text-xs leading-reading text-muted">{item.prompt}</p>
      <p className="break-words text-xs text-muted">{t('ui.academy.journal.answer', {answer: item.response})}</p>
      {item.diagnosis ? <p className="text-xs leading-reading text-muted">{t('ui.academy.journal.diagnosis', {text: item.diagnosis})}</p> : null}
      {onOpenLesson && !item.repaired ? <Button size="xs" variant="subtle" data-journal-retry={item.lesson_id} onClick={() => onOpenLesson(item.lesson_id)}>{t('ui.academy.journal.retry')}</Button> : null}
    </li>)}</ol> : <p className="text-xs text-muted">{t('ui.academy.journal.emptyOpen')}</p>}
  </div>
}
