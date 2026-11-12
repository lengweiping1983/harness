import { t } from '@/i18n'

interface CompactionProgressProps {
  completed?: number
  total?: number
  stage?: string
  attempt?: number
  maxAttempts?: number
  retryInSeconds?: number
}

export function CompactionProgress({ completed = 0, total, stage, attempt = 1, maxAttempts = 3, retryInSeconds }: CompactionProgressProps) {
  return <span className="min-w-0 break-words">
    <span className="block">{t(stage === 'merge' ? 'ui.context.mergeProgress' : 'ui.context.progress')}</span>
    <span className="mt-0.5 block text-2xs text-faint">
      {t('ui.context.durationHint')}
      {total ? <> · {t('ui.context.completedParts', { completed, total })}</> : null}
      {retryInSeconds && retryInSeconds > 0 ? <> · {t('ui.context.retryDelay', { seconds: Math.ceil(retryInSeconds) })}</>
        : attempt > 1 ? <> · {t('ui.context.retryAttempt', { attempt, max: maxAttempts })}</> : null}
    </span>
  </span>
}
