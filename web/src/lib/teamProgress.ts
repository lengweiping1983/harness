import type { TeamBlock, TeamMemberState } from '@/types'
import { readTeamHandoff } from './teamHandoff'
const terminalPhases = new Set(['done', 'failed', 'interrupted'])
const terminalMembers = new Set(['done', 'failed', 'blocked', 'interrupted'])
const phases = new Set(['start','plan','execute','summary','done','failed','interrupted'])
function sourceSnapshot(raw: unknown): Pick<NonNullable<TeamBlock['worktrees']>[number], 'source_snapshot'> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  const value = raw as Record<string, unknown>
  if (typeof value.tree !== 'string' || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(value.tree) || typeof value.status !== 'string' || !['pending', 'preparing', 'ready', 'failed'].includes(value.status)) return {}
  return {source_snapshot: {tree: value.tree, status: value.status as 'pending' | 'preparing' | 'ready' | 'failed'}}
}
export function teamMemberCounts(members: TeamMemberState[]) {
  const counts = { total: members.length, settled: 0, succeeded: 0, failed: 0, blocked: 0, interrupted: 0, running: 0, pending: 0, unknown: 0 }
  for (const member of members) {
    if (member.status === 'done') counts.succeeded++
    else if (member.status === 'failed') counts.failed++
    else if (member.status === 'blocked') counts.blocked++
    else if (member.status === 'interrupted') counts.interrupted++
    else if (member.status === 'running') counts.running++
    else if (member.status === 'pending') counts.pending++
    else counts.unknown++
  }
  counts.settled = counts.succeeded + counts.failed + counts.blocked + counts.interrupted
  return counts
}
export function readTeamProgress(raw: Record<string, unknown>, previous?: TeamBlock): TeamBlock | null {
  const runId = typeof raw.run_id === 'string' ? raw.run_id : ''
  if (!runId) return null
  if (previous?.runId === runId && Number.isSafeInteger(raw.source_seq) && previous.sourceSeq !== undefined && Number(raw.source_seq) < previous.sourceSeq) return previous
  const phase = typeof raw.phase === 'string' && phases.has(raw.phase) ? raw.phase as TeamBlock['phase'] : previous?.phase ?? 'start'
  if (previous?.runId === runId && terminalPhases.has(previous.phase) && !terminalPhases.has(phase)) return previous
  const members: TeamMemberState[] = Array.isArray(raw.members) ? raw.members.filter((row): row is Record<string, unknown> => Boolean(row && typeof row === 'object')).map(row => ({...readTeamHandoff(row), ...(['codex', 'claude', 'opencode'].includes(String(row.runner)) ? {runner: row.runner as TeamMemberState['runner']} : {}), ...(['agent_id', 'planning_agent_id', 'agent', 'context_session_id'] as const).reduce((ids, key) => typeof row[key] === 'string' && row[key] ? {...ids, [key]: row[key]} : ids, {}), displayName: typeof row.display_name === 'string' && row.display_name.trim() ? row.display_name : previous?.members.find(member => member.name === String(row.name ?? ''))?.displayName, name:String(row.name ?? ''),role:String(row.role ?? 'member'),status:String(row.status ?? 'pending'),error:typeof row.error === 'string' ? row.error : null, ...(Array.isArray(row.depends_on) ? {depends_on: row.depends_on.filter((name): name is string => typeof name === 'string')} : {})})).filter(row => row.name) : previous?.members ?? []
  const assignments = raw.assignments && typeof raw.assignments === 'object' && !Array.isArray(raw.assignments) ? Object.fromEntries(Object.entries(raw.assignments).filter((entry): entry is [string, string] => typeof entry[1] === 'string')) : previous?.assignments
  const workspaces = raw.workspaces && typeof raw.workspaces === 'object' && !Array.isArray(raw.workspaces) ? Object.fromEntries(Object.entries(raw.workspaces).filter((entry): entry is [string, string] => typeof entry[1] === 'string' && Boolean(entry[1]))) : previous?.workspaces
  const worktrees = Array.isArray(raw.worktrees) ? raw.worktrees.filter((row): row is Record<string, unknown> => Boolean(row && typeof row === 'object')).filter(row => ['id', 'path', 'team_member'].every(key => typeof row[key] === 'string' && Boolean(row[key]))).map(row => ({id: row.id as string, path: row.path as string, team_member: row.team_member as string, ...(typeof row.branch === 'string' ? {branch: row.branch} : {}), ...sourceSnapshot(row.source_snapshot)})).filter((row, index, rows) => rows.findIndex(other => other.id === row.id) === index) : previous?.worktrees
  return {
    workspaces, worktrees,
    direct: typeof raw.direct === 'boolean' ? raw.direct : previous?.direct,
    publicReply: previous?.runId === runId ? previous.publicReply : undefined,
    previews: terminalPhases.has(phase) && !(phase !== 'done' && (raw.direct === true || previous?.direct)) ? undefined : previous?.previews ? Object.fromEntries(Object.entries(previous.previews).filter(([name]) => members.some(member => member.name === name && (raw.direct === true || previous?.direct || !terminalMembers.has(member.status))))) : undefined,
    displayName: typeof raw.display_name === 'string' ? raw.display_name : previous?.displayName,
    decision: raw.decision === 'reply' || raw.decision === 'delegate' ? raw.decision : previous?.decision,
    decisionReason: typeof raw.decision_reason === 'string' ? raw.decision_reason : previous?.decisionReason,
    id: previous?.id ?? 'team:' + runId, kind:'team', runId, phase, members, assignments,
    sourceSeq: Number.isSafeInteger(raw.source_seq) && Number(raw.source_seq) >= 0 ? Number(raw.source_seq) : previous?.sourceSeq,
    at: previous?.at ?? (typeof raw.source_time === 'number' ? raw.source_time : Date.now()),
    turn: Number.isSafeInteger(raw.turn) && Number(raw.turn) > 0 ? Number(raw.turn) : previous?.turn,
    team: typeof raw.team === 'string' ? raw.team : previous?.team ?? '',
    task: typeof raw.task === 'string' ? raw.task : previous?.task,
    summary: typeof raw.summary === 'string' ? raw.summary : previous?.summary,
    error: typeof raw.error === 'string' ? raw.error : previous?.error,
    fallback: typeof raw.fallback === 'boolean' ? raw.fallback : previous?.fallback,
    retried: typeof raw.retried === 'boolean' ? raw.retried : previous?.retried,
    recovered: typeof raw.recovered === 'boolean' ? raw.recovered : previous?.recovered,
  }
}


/** 独立预览使用完整快照，不进入普通助手草稿；旧版本和终态后增量忽略。 */
export function readMemberStream(raw: Record<string, unknown>, block: TeamBlock): TeamBlock {
  if (raw.run_id !== block.runId || ['done','failed','interrupted'].includes(block.phase) || typeof raw.member !== 'string' || typeof raw.agent_id !== 'string' || typeof raw.request_id !== 'string' || typeof raw.version !== 'number' || !Number.isSafeInteger(raw.version) || raw.version < 1 || !['streaming','cleared'].includes(String(raw.status)) || typeof raw.text !== 'string' || raw.text.length > 24000) return block
  const member = block.members.find(row => row.name === raw.member)
  if (!member || terminalMembers.has(member.status) || (raw.stage === 'planning' ? member.planning_agent_id !== raw.agent_id || Boolean(member.agent_id && member.agent_id !== raw.agent_id) : member.agent_id && member.agent_id !== raw.agent_id)) return block
  const previous = block.previews?.[raw.member]
  if (previous?.agentId === raw.agent_id && raw.version <= previous.version) return block
  const publicReply = member.role === 'lead' && ['reply', 'summary'].includes(String(raw.stage)) && raw.status === 'streaming' && raw.text
    ? { member: member.name, displayName: member.displayName || member.name, agent: member.agent, agentId: raw.agent_id, requestId: raw.request_id, text: raw.text, stage: raw.stage as 'reply' | 'summary', truncated: raw.truncated === true }
    : block.publicReply
  return {...block, publicReply, previews: {...block.previews, [raw.member]: {agentId: raw.agent_id, requestId: raw.request_id, version: raw.version, stage: ['member', 'reply', 'summary', 'planning'].includes(String(raw.stage)) ? raw.stage as 'member' | 'reply' | 'summary' | 'planning' : undefined, thinking: raw.status === 'cleared' ? '' : typeof raw.thinking === 'string' ? raw.thinking.slice(-12000) : undefined, thinkingTruncated: raw.thinking_truncated === true, text: raw.status === 'cleared' ? '' : raw.text, status: raw.status as 'streaming' | 'cleared', truncated: raw.truncated === true}}}
}
