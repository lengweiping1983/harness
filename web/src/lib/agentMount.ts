import { t } from '@/i18n'
import type { AgentSelectionState, AgentTeamRecipient, TeamRoute } from './protocol.generated'
export interface TeamAddress { team: string; selectionRevision: number; members: string[]; identities: Record<string, string> }
export type TeamRecipient = AgentTeamRecipient

export type AgentMountState = AgentSelectionState

/** 挂载版本来自服务端日志，迟到快照不能覆盖更新的挂载或退场。 */
export function mergeAgentMount(current: AgentMountState | null, value: unknown): AgentMountState | null {
  if (!value || typeof value !== 'object') return current
  const state = value as Record<string, unknown>
  if (typeof state.available !== 'boolean' || !(state.agent === null || typeof state.agent === 'string')
      || typeof state.revision !== 'number' || !Number.isSafeInteger(state.revision) || state.revision < 0) return current
  if (current && state.revision < current.revision) return current
  if (state.members !== undefined && (!Array.isArray(state.members) || !state.members.every(row => row && typeof row === 'object'
    && typeof row.identity === 'string' && /^[a-f0-9]{64}$/.test(row.identity)
    && typeof row.name === 'string' && typeof row.display_name === 'string' && (row.role === 'lead' || row.role === 'member')))) return current
  return { available: state.available, agent: state.available ? state.agent as string | null : null,
    revision: state.revision, description: typeof state.description === 'string' ? state.description : undefined,
    display_name: typeof state.display_name === 'string' ? state.display_name : undefined,
    profession: typeof state.profession === 'string' ? state.profession : undefined,
    agent_type: state.agent_type === 'team' ? 'team' : state.agent_type === 'agent' ? 'agent' : undefined,
    selection_unavailable: state.selection_unavailable === true,
    team_worktrees: state.team_worktrees === true, team_working_copy: state.team_working_copy === true,
    members: state.members as TeamRecipient[] | undefined,
    runner: state.runner === 'codex' || state.runner === 'claude' || state.runner === 'opencode' ? state.runner : undefined,
    model: typeof state.model === 'string' ? state.model : null }
}

/** 当前团队选择是否仍与草稿一致；空收件人是待选择状态，不能发送。 */
function currentTeamSelection(address: TeamAddress, mount: AgentMountState | null): boolean {
  return mount?.available === true && mount.agent_type === 'team' && !mount.selection_unavailable
    && address.team === mount.agent && address.selectionRevision === mount.revision
}

/** 选择时冻结收件人的配置指纹。 */
export function makeTeamAddress(mount: AgentMountState, members: string[]): TeamAddress {
  const identities = Object.fromEntries(members.map(name => [name, mount.members?.find(member => member.name === name)?.identity]))
  if (Object.values(identities).some(value => typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value))) {
    throw new Error(t('ui.team.recipientInvalid'))
  }
  return {team: mount.agent!, selectionRevision: mount.revision, members, identities: identities as Record<string, string>}
}

/** 收件地址必须与当前团队、选择版本及成员身份一致。 */
export function validTeamAddress(address: TeamAddress | undefined, mount: AgentMountState | null): boolean {
  if (!address) return true
  if (!address.identities || typeof address.identities !== 'object' || Array.isArray(address.identities)) return false
  return currentTeamSelection(address, mount) && Array.isArray(address.members) && address.members.length > 0
    && new Set(address.members).size === address.members.length
    && Object.keys(address.identities).length === address.members.length
    && address.members.every(name => typeof address.identities[name] === 'string'
      && /^[a-f0-9]{64}$/.test(address.identities[name])
      && mount?.members?.some(member => member.name === name && address.identities[name] === member.identity) === true)
}

/** 多位收件人必须明确显示，不能在草稿恢复后误呈现为仅第一位。 */
export function teamRecipientValue(address: TeamAddress | undefined, mount: AgentMountState | null): string {
  if (address && currentTeamSelection(address, mount) && Array.isArray(address.members) && address.members.length === 0) return '@empty'
  if (!validTeamAddress(address, mount)) return '@stale'
  if (!address) return ''
  if (address.members.length === (mount?.members?.length ?? 0) && address.members.length > 1) return '@all'
  return address.members.length > 1 ? '@custom' : address.members[0]
}

/** 排队地址直接使用任务冻结的选择版本和成员指纹。 */
export function queuedTeamAddress(route: TeamRoute): TeamAddress {
  return {team: route.team, members: [...route.recipients], selectionRevision: route.selection_revision,
    identities: {...route.recipient_identities}}
}

/** 当前身份匹配时显示配置名称，否则显示任务冻结的成员标识。 */
export function queuedRecipientPresentation(route: TeamRoute, mount: AgentMountState | null): {names: string[]; current: boolean} {
  const current = validTeamAddress(queuedTeamAddress(route), mount)
  return {current, names: route.recipients.map(name => current ? mount?.members?.find(member => member.name === name)?.display_name || name : name)}
}
