import {Loader2} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {useSessionRunning} from '@/store/sessionActivity'

export function SessionRunningIndicator({sessionId}: {sessionId: string | null}) {
  useLocale()
  const running = useSessionRunning(sessionId)
  if (!sessionId || !running) return null
  return <span role="status" aria-label={t('ui.session.running')} title={t('ui.session.running')} className="inline-flex shrink-0 text-muted">
    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
  </span>
}
