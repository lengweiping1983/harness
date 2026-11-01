import { validTeamAddress, type AgentMountState, type TeamAddress } from './agentMount'
import type { TeamMessageAuthor } from './protocol.generated'
export type { TeamMessageAuthor }
/** 呈现用名称与角色不携带执行身份凭据。 */
export type TeamAuthorPresentation = Omit<TeamMessageAuthor, 'identity'>

/** 使用消息保存的作者引用，切换当前团队不会改变历史消息的头像身份。 */
export function teamAuthorAvatar(author: TeamAuthorPresentation, sessionId?: string | null): string | undefined {
  const agent = author.agent
  if (!agent) return undefined
  return '/api/v1/plugins/agents/' + encodeURIComponent(agent) + '/avatar' + (sessionId ? '?session_id=' + encodeURIComponent(sessionId) : '')
}

/** 作者来自持久化消息快照，缺失或畸形数据按普通助手回复展示。 */
export function readTeamAuthor(value: unknown): TeamMessageAuthor | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return
  const data = value as Record<string, unknown>
  if (typeof data.team !== 'string' || !data.team || typeof data.member !== 'string' || !data.member ||
      typeof data.display_name !== 'string' || !data.display_name ||
      typeof data.identity !== 'string' || !/^[a-f0-9]{64}$/.test(data.identity) ||
      (data.role !== 'lead' && data.role !== 'member') || (data.stage !== 'member' && data.stage !== 'summary' && data.stage !== 'reply')) return
  return { team: data.team, member: data.member, display_name: data.display_name,
    role: data.role as TeamMessageAuthor['role'], stage: data.stage as TeamMessageAuthor['stage'],
    ...(typeof data.agent === 'string' ? { agent: data.agent } : {}),
    identity: data.identity }
}


export interface TeamMessageRecipients {
  team: string
  display_name: string
  members: Array<{ name: string; display_name: string; identity: string }>
}

/** 收件人采用发送时的完整身份快照。 */
export function readTeamRecipients(value: unknown): TeamMessageRecipients | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return
  const data = value as Record<string, unknown>
  if (typeof data.team !== 'string' || !data.team.trim() || typeof data.display_name !== 'string' || !data.display_name.trim()
    || !Array.isArray(data.members) || !data.members.length || data.members.length > 8) return
  const members: TeamMessageRecipients['members'] = []
  const names = new Set<string>()
  for (const member of data.members) {
    if (!member || typeof member !== 'object' || typeof member.name !== 'string' || !member.name.trim()
      || typeof member.identity !== 'string' || !/^[a-f0-9]{64}$/.test(member.identity)
      || typeof member.display_name !== 'string' || !member.display_name.trim() || names.has(member.name)) return
    names.add(member.name)
    members.push({name: member.name, display_name: member.display_name,
      identity: member.identity})
  }
  return {team: data.team, display_name: data.display_name, members}
}


/** 编辑分支沿用历史收件人；无法确认身份时保留失效选择，禁止静默改成自动调度。 */
export function editedMessageTeamAddress(recipients: TeamMessageRecipients | undefined, mount: AgentMountState | null): TeamAddress | undefined {
  if (!recipients) return
  const identities = Object.fromEntries(recipients.members.map(member => [member.name, member.identity]))
  const address: TeamAddress = {team: recipients.team, members: recipients.members.map(member => member.name),
    selectionRevision: mount?.revision ?? -1, identities}
  return validTeamAddress(address, mount) ? address : {...address, selectionRevision: -1}
}
