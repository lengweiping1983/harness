import { Check, CircleHelp, Clock3, Loader2, Minus, Pause, X } from 'lucide-react'
import { Badge, type Tone } from '@/components/ui/primitives'
import { t, useLocale } from '@/i18n'
import { REQUEST_STATUS } from '@/lib/requestTraces'

function presentation(status: string): { tone: Tone; icon: typeof Check } {
  switch (status) {
    case 'completed':
    case 'done':
      return { tone: 'ok', icon: Check }
    case 'failed':
    case 'error':
      return { tone: 'danger', icon: X }
    case 'started':
    case 'running':
      return { tone: 'brand', icon: Loader2 }
    case 'queued':
    case 'pending':
      return { tone: 'neutral', icon: Clock3 }
    case 'interrupted':
    case 'stopping':
      return { tone: 'warn', icon: Pause }
    case 'cancelled':
    case 'stopped':
    case 'skipped':
      return { tone: 'neutral', icon: Minus }
    default:
      return { tone: 'neutral', icon: CircleHelp }
  }
}

export function TraceStatusBadge({ status }: { status: string }) {
  useLocale()
  const { tone, icon: Icon } = presentation(status)
  return <Badge tone={tone} icon={<Icon aria-hidden className="h-3 w-3 shrink-0" />}>
    {REQUEST_STATUS[status] ?? t('ui.trace.status.' + status)}
  </Badge>
}
