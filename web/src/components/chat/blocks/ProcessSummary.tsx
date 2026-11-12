import type {ButtonHTMLAttributes, ReactNode} from 'react'
import {Brain, ChevronRight} from 'lucide-react'
import {ToolActivityIcon} from '@/components/ui/ToolActivityIcon'
import {cn} from '@/lib/utils'
import type {ToolRunRow} from '@/lib/toolRuns'
import {t, useLocale} from '@/i18n'

type SummaryProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'aria-expanded'> & {
  children: ReactNode
  icon?: ReactNode
  expanded?: boolean
}

/** 轮次用时与过程分组共用字号、密度、收展箭头及可换行的内容通道。 */
export function ProcessSummaryButton({children, icon, expanded, className, ...props}: SummaryProps) {
  return <button {...props} type="button" aria-expanded={expanded} className={cn(
    'flex max-w-full min-h-6 min-w-0 items-center gap-1.5 rounded-md px-1 py-0.5 text-left text-xs font-normal leading-5 text-muted',
    'transition-colors duration-150 hover:bg-raised/70 hover:text-ink focus-visible:bg-raised/70 focus-visible:outline-brand active:bg-raised disabled:cursor-default disabled:hover:bg-transparent disabled:hover:text-muted',
    className,
  )}>
    {icon}
    <span data-process-summary-content="" className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5">{children}</span>
    {expanded !== undefined ? <ChevronRight aria-hidden className={cn('h-3.5 w-3.5 shrink-0 transition-transform duration-200', expanded && 'rotate-90')} /> : null}
  </button>
}

/** 状态计数与统计处于同一排版层级，只在实际可用宽度不足时换行。 */
export function ProcessGroupSummary({row, onClick}: {row: ToolRunRow; onClick: SummaryProps['onClick']}) {
  useLocale()
  return <ProcessSummaryButton expanded={row.open} onClick={onClick}
    aria-label={t('ui.components.chat.Conversation.114bc8bcfc39', {p0: row.summary})}
    title={t('ui.components.chat.Conversation.4203e873a132')} className="w-full bg-raised/40"
    icon={row.thoughts === row.count && !row.running
      ? <Brain aria-hidden className="h-3.5 w-3.5 shrink-0" />
      : <ToolActivityIcon kind={row.activityKind} running={!!row.running} failed={!!row.failed} className={cn('h-3.5 w-3.5 shrink-0', row.failed && 'text-danger')} />}>
    <span className="min-w-0 break-words">{row.label}</span>
    <span className="min-w-0 break-words text-faint">{row.summary}</span>
    {row.running ? <span className="whitespace-nowrap tabular-nums text-faint">{t('ui.process.runningCount', {count: row.running})}</span> : null}
    {row.failed ? <span className="whitespace-nowrap tabular-nums text-danger">{t('ui.process.failedCount', {count: row.failed})}</span> : null}
    {row.skipped ? <span className="whitespace-nowrap tabular-nums text-faint">{t('ui.process.skippedCount', {count: row.skipped})}</span> : null}
    {row.background ? <span className="whitespace-nowrap tabular-nums text-faint">{t('ui.process.backgroundCount', {count: row.background})}</span> : null}
  </ProcessSummaryButton>
}
