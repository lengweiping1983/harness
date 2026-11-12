import type {TeamBlock} from '@/types'

export function canIntegrateTeam(block: TeamBlock): boolean {
  return ['done', 'failed', 'interrupted'].includes(block.phase) && Number.isSafeInteger(block.sourceSeq) && Boolean(block.worktrees?.length) && block.worktrees!.every(row => {
    const member = block.members.find(item => item.name === row.team_member)
    return member?.status === 'done' && Boolean(member.delivery?.tree)
  })
}
