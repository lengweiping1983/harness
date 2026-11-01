import { t } from '@/i18n'
import { cn } from '@/lib/utils'

export function RequestNumberBadge({ number, className }: { number: number; className?: string }) {
  return <span role="img" aria-label={t('ui.trace.requestNumber', { number })}
    className={cn('flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-brand bg-brand text-2xs font-semibold text-brand-ink', className)}>{number}</span>
}
