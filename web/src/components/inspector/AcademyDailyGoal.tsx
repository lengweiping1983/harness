import {useEffect, useState} from 'react'
import {Check, Target} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import type {AcademyActivityDay} from '@/types'
import {ACADEMY_GOAL_EVENT, academyDailyGoalRead, academyDailyGoalWrite, academyTodayEvidence} from '@/lib/academyGoal'
import {cn} from '@/lib/utils'
import {Button} from '@/components/ui/primitives'

/** 每日证据目标：自我调节学习的前思—监控—达成闭环；只计真实证据（作答/检查/重测），非登录或点击等虚荣指标。 */
export function AcademyDailyGoal({activity, compact = false}: {activity?: ReadonlyArray<AcademyActivityDay>; compact?: boolean}) {
  useLocale()
  const [goal, setGoal] = useState<number | null>(() => academyDailyGoalRead())
  const [editing, setEditing] = useState(false)
  useEffect(() => {
    const onGoal = () => setGoal(academyDailyGoalRead())
    window.addEventListener(ACADEMY_GOAL_EVENT, onGoal)
    return () => window.removeEventListener(ACADEMY_GOAL_EVENT, onGoal)
  }, [])
  const evidence = academyTodayEvidence(activity)
  const attained = goal != null && evidence >= goal
  const apply = (next: number | null) => {setGoal(next); academyDailyGoalWrite(next); setEditing(false)}
  return <div className="flex flex-wrap items-center gap-1" data-daily-goal-root>
    {goal == null
      ? <Button size="xs" variant="ghost" data-daily-goal="unset" onClick={() => setEditing(true)} icon={<Target className="h-3 w-3" />}>{t('ui.academy.goal.set')}</Button>
      : <button type="button" data-daily-goal={attained ? 'attained' : 'active'} onClick={() => setEditing(true)}
          aria-label={t('ui.academy.goal.chip', {done: evidence, goal}) + (attained ? ' · ' + t('ui.academy.goal.attained') : '')}
          className={cn('flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs', attained ? 'border-ok/40 bg-ok/10 text-ok' : 'border-line text-muted')}>
          {attained ? <Check className="h-3 w-3" aria-hidden /> : <Target className="h-3 w-3" aria-hidden />}
          {t('ui.academy.goal.chip', {done: evidence, goal})}
        </button>}
    {editing ? <div role="group" aria-label={t('ui.academy.goal.editLabel')} className="flex flex-wrap items-center gap-1" data-goal-presets>
      {[3, 5, 10].map(value => <button key={value} type="button" data-goal-preset={value} aria-pressed={goal === value} onClick={() => apply(value)}
        className={cn('rounded-full border px-2.5 py-1 text-xs', goal === value ? 'border-brand bg-brand/10 font-medium text-brand' : 'border-line text-muted')}>{value}</button>)}
      <button type="button" data-goal-preset="off" aria-pressed={goal == null} onClick={() => apply(null)}
        className={cn('rounded-full border px-2.5 py-1 text-xs', goal == null ? 'border-brand bg-brand/10 font-medium text-brand' : 'border-line text-muted')}>{t('ui.academy.goal.presetOff')}</button>
    </div> : null}
    {!compact ? <p className="w-full text-xs leading-reading text-muted">{t('ui.academy.goal.hint')}</p> : null}
  </div>
}
