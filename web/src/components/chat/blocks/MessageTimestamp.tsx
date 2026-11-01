import {getLocale, t, useLocale} from '@/i18n'
import {formatClock, toDate} from '@/lib/utils'

/** 消息发送与轮次记录共用时间格式，完整时间及事件含义供提示和辅助技术读取。 */
export function MessageTimestamp({at, kind, className}: {at: number; kind: 'sent' | 'completed'; className?: string}) {
  useLocale()
  const date = toDate(at)
  if (!date) return null
  const label = t(kind === 'sent' ? 'ui.messageTime.sent' : 'ui.messageTime.completed', {time: date.toLocaleString(getLocale())})
  return <time dateTime={date.toISOString()} title={label} aria-label={label} className={className}>{date.toLocaleDateString(getLocale(), {year: 'numeric', month: '2-digit', day: '2-digit'})} {formatClock(at)}</time>
}
