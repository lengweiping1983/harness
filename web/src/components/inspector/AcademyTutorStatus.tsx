import {t, useLocale} from '@/i18n'
import type {AcademyTutorTurn} from '@/types'
import {Spinner} from '@/components/ui/primitives'

/** 状态只来自已读取的工具记录，不把等待时间或工具结束推断为带练完成。 */
export function AcademyTutorStatus({turn}: {turn: AcademyTutorTurn}) {
  useLocale()
  if (turn.status !== 'running') return null
  const unknown = turn.actions.some(action => action.status === 'unknown')
  const action = turn.actions.find(action => action.status === 'running')
  return <p role="status" className={'flex items-center gap-2 text-sm ' + (unknown ? 'text-warn' : 'text-muted')}>
    {unknown ? null : <Spinner />}
    {t(unknown ? 'ui.academy.error.outcome-unknown' : turn.stopping ? 'ui.academy.tutor.stopping' : action ? 'ui.academy.tutor.working.' + action.tool : 'ui.academy.tutor.running')}
  </p>
}
