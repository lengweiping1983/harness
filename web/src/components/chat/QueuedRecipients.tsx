import {t, useLocale} from '@/i18n'
import {queuedRecipientPresentation, type AgentMountState} from '@/lib/agentMount'
import type {TeamRoute} from '@/lib/protocol.generated'

/** 主输入区和任务队列使用同一目标展示，切换团队不改写旧任务的身份。 */
export function QueuedRecipients({route, mount}: {route: TeamRoute; mount: AgentMountState | null}) {
  useLocale()
  const {names, current} = queuedRecipientPresentation(route, mount)
  return <span className={'mb-1 block text-xs ' + (current ? 'text-brand' : 'text-warn')} title={current ? undefined : t('ui.team.recipients.queuedChangedHint')}>
    {t('ui.team.recipients.label')} · {!current && <>{t('ui.team.recipients.originalTeam', {team: route.team})} · </>}{names.join(' · ')}
    {!current && <span className="ml-2">{t('ui.team.recipients.queuedChanged')}</span>}
  </span>
}
