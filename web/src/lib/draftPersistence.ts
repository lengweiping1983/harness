import {isRecord} from './guards'
import {hasTranslation} from '@/i18n'
import {responseDefinitions} from './responses.generated'

/** 只接受当前草稿快照；校验发生在 Zustand 合并和自动保存之前。 */
type Row = Record<string, unknown>
const text = (value: unknown) => typeof value === 'string'
const integer = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0
const fields = (row: Row, required: string[], optional: string[] = []) => required.every(key => Object.hasOwn(row, key)) && Object.keys(row).every(key => required.includes(key) || optional.includes(key))
const optional = (row: Row, key: string, check: (value: unknown) => boolean) => !Object.hasOwn(row, key) || check(row[key])
const strings = (value: unknown) => Array.isArray(value) && value.every(text)
const dictionary = (value: unknown, check: (value: unknown) => boolean) => isRecord(value) && Object.values(value).every(check)
const mode = (value: unknown) => ['normal', 'plan', 'goal'].includes(value as string)
const resourceKey = (value: unknown) => typeof value === 'string' && hasTranslation(value)
const scalar = (value: unknown) => text(value) || typeof value === 'number' && Number.isFinite(value)
function resource(value: unknown): boolean {
  const pending: unknown[] = [value]
  while (pending.length) {
    const item = pending.pop()
    if (text(item)) continue
    if (!isRecord(item)) return false
    if (fields(item, ['key'], ['params'])) {
      if (!resourceKey(item.key) || !optional(item, 'params', params => dictionary(params, scalar))) return false
      continue
    }
    if (!fields(item, ['template']) || !isRecord(item.template) || !fields(item.template, ['key', 'params'])
      || !resourceKey(item.template.key) || !isRecord(item.template.params)) return false
    for (const child of Object.values(item.template.params)) {
      if (typeof child === 'number' && Number.isFinite(child)) continue
      pending.push(child)
    }
  }
  return true
}
function agent(value: unknown): boolean {
  return isRecord(value) && fields(value, ['name', 'agent_type'], ['display_name']) && text(value.name)
    && ['agent', 'team'].includes(value.agent_type as string) && optional(value, 'display_name', text)
}
function address(value: unknown): boolean {
  return isRecord(value) && fields(value, ['team', 'selectionRevision', 'members', 'identities']) && text(value.team)
    && integer(value.selectionRevision) && strings(value.members) && dictionary(value.identities, text)
}
function attachment(value: unknown, draft: boolean): boolean {
  if (!isRecord(value) || !fields(value, ['name', 'mediaType', 'size', 'isImage', draft ? 'localId' : 'attachmentId'],
    draft ? ['attachmentId', 'previewUrl', 'status', 'error'] : [])) return false
  return text(value.name) && text(value.mediaType) && integer(value.size) && typeof value.isImage === 'boolean'
    && text(value[draft ? 'localId' : 'attachmentId']) && optional(value, 'attachmentId', text)
    && optional(value, 'previewUrl', text) && optional(value, 'error', resource)
    && optional(value, 'status', status => ['pending', 'uploading', 'error'].includes(status as string))
}
const selection = (value: Row) => optional(value, 'executionMode', mode) && optional(value, 'agentSelection', agent) && optional(value, 'teamAddress', address)
function draft(value: unknown): boolean {
  return isRecord(value) && fields(value, ['text', 'attachments'], ['editOrigin', 'inlineEdits', 'teamAddress', 'agentSelection', 'executionMode', 'goalCreation', 'creationRequest'])
    && text(value.text) && Array.isArray(value.attachments) && value.attachments.every(item => attachment(item, true)) && selection(value)
    && optional(value, 'inlineEdits', edits => dictionary(edits, text))
    && optional(value, 'editOrigin', origin => isRecord(origin) && fields(origin, ['sessionId', 'sourceSeq']) && text(origin.sessionId) && integer(origin.sourceSeq))
    && optional(value, 'creationRequest', request => isRecord(request) && fields(request, ['id'], ['cwd', 'agent', 'project'])
      && typeof request.id === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(request.id) && optional(request, 'cwd', text) && optional(request, 'agent', text)
      && optional(request, 'project', project => isRecord(project) && fields(project, ['workspace_id', 'mode'], ['ref', 'expected_head', 'expected_branch'])
        && text(project.workspace_id) && Boolean(project.workspace_id) && ['local', 'worktree'].includes(project.mode as string)
        && optional(project, 'ref', text) && optional(project, 'expected_head', item => item === null || text(item))
        && optional(project, 'expected_branch', item => item === null || text(item))))
    && optional(value, 'goalCreation', goal => isRecord(goal) && fields(goal, ['objective', 'rounds', 'budget']) && Object.values(goal).every(text))
}
function submission(value: unknown): boolean {
  return isRecord(value) && fields(value, ['id', 'text', 'attachments', 'action', 'status'], ['executionMode', 'agentSelection', 'sendOnReady', 'error', 'teamAddress'])
    && text(value.id) && text(value.text) && Array.isArray(value.attachments) && value.attachments.every(item => attachment(item, false))
    && ['send', 'steer'].includes(value.action as string) && ['sending', 'unknown', 'rejected', 'unapplied'].includes(value.status as string)
    && selection(value) && optional(value, 'sendOnReady', item => typeof item === 'boolean') && optional(value, 'error', resource)
}
function persistedState(raw: string, required: string[], check: (state: Row) => boolean): boolean {
  try {
    const value: unknown = JSON.parse(raw)
    return isRecord(value) && fields(value, ['state', 'version']) && value.version === 0
      && isRecord(value.state) && fields(value.state, required) && check(value.state)
  } catch { return false }
}
export function validPersistedDrafts(raw: string): boolean {
  return persistedState(raw, ['bySession', 'submissions'], state => dictionary(state.bySession, draft)
    && dictionary(state.submissions, items => Array.isArray(items) && items.every(submission)))
}
const queueFields = [...Object.keys(responseDefinitions.RunRecord.properties), ...Object.keys(responseDefinitions.QueueEntry.properties)]
function queueEntry(value: unknown, sessionId: string): value is Row {
  // 当前服务端还携带 RunRecord 元信息；只解释已声明的队列操作字段。
  return isRecord(value) && fields(value, ['kind','id','text','edit_version'], queueFields) && value.kind === 'runs' && text(value.id) && Boolean(value.id) && text(value.text)
    && typeof value.edit_version === 'string' && /^[a-f0-9]{64}$/.test(value.edit_version)
    && optional(value, 'session_id', id => id === sessionId)
    && optional(value, 'goal_id', id => id === null || text(id))
    && optional(value, 'attachment_refs', refs => Array.isArray(refs) && refs.every(item => attachment(item, false)))
    && optional(value, 'requester', requester => isRecord(requester) && text(requester.subject) && text(requester.display_name))
    && optional(value, 'team_route', route => isRecord(route) && text(route.team) && text(route.revision)
      && integer(route.selection_revision) && strings(route.recipients) && dictionary(route.recipient_identities, text))
}
function queueEdit(value: unknown, sessionId: string): boolean {
  if (!isRecord(value) || !fields(value, ['entry', 'draft'], ['unconfirmed']) || !queueEntry(value.entry, sessionId) || !text(value.draft)) return false
  const id = value.entry.id
  return optional(value, 'unconfirmed', pending => isRecord(pending) && fields(pending, ['entry', 'text'])
    && queueEntry(pending.entry, sessionId) && pending.entry.id === id && text(pending.text))
}
export function validPersistedQueueEdits(raw: string): boolean {
  return persistedState(raw, ['bySession', 'withdrawals'], state => isRecord(state.bySession) && isRecord(state.withdrawals)
    && Object.entries(state.bySession).every(([id, value]) => queueEdit(value, id))
    && Object.entries(state.withdrawals).every(([id, value]) => queueEntry(value, id)))
}
