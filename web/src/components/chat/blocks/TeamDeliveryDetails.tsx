import { t } from '@/i18n'
import type { TeamBlock, TeamMemberState } from '@/types'

export function TeamDeliveryDetails({member, worktrees}: {member?: TeamMemberState; worktrees: NonNullable<TeamBlock['worktrees']>}) {
  if (!member) return null
  const version = member.delivery?.tree
  return <div className="min-w-0 space-y-2 text-2xs [overflow-wrap:anywhere]">
    {version && <p className="text-ok">{t('ui.team.delivery.frozen')} <code title={version}>{version.slice(0, 12)}</code></p>}
    {Boolean(member.delivery_inputs?.sources.length) && <div className="space-y-1">
      <p className="text-muted">{t('ui.team.delivery.received')}</p>
      {member.delivery_inputs?.sources.map(source => <p key={source.id} className="text-brand">
        {worktrees.find(row => row.id === source.id)?.team_member ?? source.id} <code title={source.tree} className="text-muted">{source.tree.slice(0, 12)}</code>
      </p>)}
    </div>}
    {member.handoff_error && <div role="alert" className="space-y-1 rounded border border-danger/20 bg-danger/5 p-2 text-danger">
      <p className="font-medium">{t('ui.team.delivery.failed')}</p>
      <p>{member.handoff_error.message}</p>
      {member.handoff_error.code && <code className="block">{member.handoff_error.code}</code>}
      {member.handoff_error.conflicts.map((path, index) => <p key={index} className="font-mono">{path}</p>)}
    </div>}
  </div>
}
