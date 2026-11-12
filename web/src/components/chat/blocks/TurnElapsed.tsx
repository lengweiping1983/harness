import { useEffect, useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { formatElapsedDuration } from '@/lib/utils'
import { usePageVisible } from '@/hooks/usePageVisible'
import { turnElapsed, type TurnActivity } from '@/lib/turnActivity'

const LABELS = {
  working: ['ui.turnElapsed.working', 'ui.turnElapsed.workingUnknown'],
  unknown: ['ui.turnElapsed.unknown', 'ui.turnElapsed.unknown'],
  completed: ['ui.turnElapsed.completed', 'ui.turnElapsed.completedUnknown'],
  interrupted: ['ui.turnElapsed.interrupted', 'ui.turnElapsed.interruptedUnknown'],
  stopped: ['ui.turnElapsed.stopped', 'ui.turnElapsed.stoppedUnknown'],
  ended: ['ui.turnElapsed.ended', 'ui.turnElapsed.endedUnknown'],
} as const

/** 计时仅更新本行，折叠与正文流不会重启时钟或重算整个虚拟列表。 */
export function TurnElapsed({ activity, active }: { activity: TurnActivity; active: boolean }) {
  useLocale()
  const visible = usePageVisible()
  const ticking = active && activity.endedAt === undefined && activity.endSeq === undefined
  const anchor = useMemo(() => ({ wall: Date.now(), monotonic: performance.now() }), [activity.turn, activity.startedAt])
  const [clock, setClock] = useState(() => ({anchor, now: anchor.wall}))
  useEffect(() => {
    if (!ticking || !visible) return
    const update = () => {
      if (!document.hidden) setClock({anchor, now: anchor.wall + Math.max(0, performance.now() - anchor.monotonic)})
    }
    update()
    const timer = window.setInterval(update, 1000)
    return () => window.clearInterval(timer)
  }, [ticking, visible, anchor])
  const elapsed = turnElapsed(activity, ticking ? clock.anchor === anchor ? clock.now : anchor.wall : undefined)
  const duration = elapsed === null ? null : formatElapsedDuration(elapsed)
  const key = ticking ? 'working' : activity.endedAt === undefined || activity.endSeq === undefined ? 'unknown'
    : activity.reason === 'completed' ? 'completed' : activity.reason === 'interrupted' ? 'interrupted' : ['aborted', 'cancelled'].includes(activity.reason ?? '') ? 'stopped' : 'ended'
  return <span data-turn-elapsed={activity.turn} data-timing-active={ticking && visible ? 'true' : 'false'}
    title={t('ui.turnElapsed.hint')} className="inline-flex min-w-0 items-center gap-1.5 tabular-nums">
    {ticking ? <Loader2 aria-hidden className="h-3.5 w-3.5 shrink-0 animate-spin motion-reduce:animate-none" /> : null}
    <span>{t(LABELS[key][duration === null ? 1 : 0], { duration: duration ?? '' })}</span>
  </span>
}
