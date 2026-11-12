import type { AgentSkillSelection } from '@/lib/protocol.generated'
export type AgentDraft = {
  version: 5
  memberNameEdits?: Record<string, string>
  skills?: AgentSkillSelection
  name: string; displayName: string; profession: string; generatedName: string
  description: string; prompt: string; model: string; runner: string
  personalityPrompt: string | null; metadataText: string; members: string
  kind: 'agent' | 'team'; scope: 'user' | 'project'; useNow: boolean; editRevision: string | null
}

export function agentDraftKey(sessionId: string | null, target: string): string {
  return `harness:agent-draft:${sessionId === null ? 'library' : 'session-' + encodeURIComponent(sessionId)}:${encodeURIComponent(target)}`
}

/** 保留尚未写完的 JSON 文本，但只恢复已知字段和合法的编辑版本。 */
export function readAgentDraft(raw: string | null): AgentDraft | null {
  if (!raw || raw.length > 524288) return null
  try {
    const row = JSON.parse(raw)
    if (!row || typeof row !== 'object' || Array.isArray(row) || row.version !== 5) return null
    const limits = {name: 120, displayName: 120, profession: 120, generatedName: 120, description: 1000, prompt: 256000, model: 1000, runner: 20, metadataText: 256000, members: 256000}
    const fields: Record<string, string> = {}
    for (const [key, limit] of Object.entries(limits)) {
      if (typeof row[key] !== 'string' || row[key].length > limit) return null
      fields[key] = row[key]
    }
    if (!['', 'codex', 'claude', 'opencode'].includes(row.runner)) return null
    if (!['agent', 'team'].includes(row.kind) || !['user', 'project'].includes(row.scope) || typeof row.useNow !== 'boolean') return null
    if (row.skills != null && (!Array.isArray(row.skills) || row.skills.length > 1000 || row.skills.some((name: unknown) => typeof name !== 'string' || name.length > 200) || new Set(row.skills).size !== row.skills.length)) return null
    if (row.personalityPrompt !== null && (typeof row.personalityPrompt !== 'string' || row.personalityPrompt.length > 1000)) return null
    if (row.editRevision !== null && (typeof row.editRevision !== 'string' || !/^[a-f0-9]{64}$/.test(row.editRevision))) return null
    let memberNameEdits: Record<string, string> | undefined
    if (row.memberNameEdits !== undefined) {
      if (!row.memberNameEdits || typeof row.memberNameEdits !== 'object' || Array.isArray(row.memberNameEdits)) return null
      const entries = Object.entries(row.memberNameEdits)
      if (entries.length > 8 || entries.some(([key, value]) => key.length > 120 || typeof value !== 'string' || value.length > 120)) return null
      memberNameEdits = Object.fromEntries(entries) as Record<string, string>
    }
    return { ...fields, skills: row.skills ?? null, ...(memberNameEdits ? {memberNameEdits} : {}), version: 5, kind: row.kind, scope: row.scope, useNow: row.useNow, editRevision: row.editRevision, personalityPrompt: row.personalityPrompt } as AgentDraft
  } catch { return null }
}

/** 写入失败保留上一份可恢复草稿，状态只描述本次实际写入结果。 */
export function persistAgentDraft(storage: Pick<Storage, 'setItem' | 'removeItem'>, key: string, serialized: string, baseline: string): 'unchanged' | 'saved' | 'invalid' | 'unavailable' {
  try {
    if (serialized === baseline) {
      storage.removeItem(key)
      return 'unchanged'
    }
    if (!readAgentDraft(serialized)) return 'invalid'
    storage.setItem(key, serialized)
    return 'saved'
  } catch { return 'unavailable' }
}

/** 固定字段顺序并统一可选草稿字段，避免无修改时误判为新草稿。 */
export function serializeAgentDraft(row: AgentDraft): string {
  return JSON.stringify(Object.fromEntries(Object.entries({...row, skills: row.skills ?? null, memberNameEdits: row.memberNameEdits ?? {}}).sort(([left], [right]) => left.localeCompare(right))))
}

/** 仅清理被删除配置的编辑草稿，不影响新建、独立副本或其他 Agent。 */
export function removeAgentEditDrafts(storage: Pick<Storage, 'length' | 'key' | 'removeItem'>, scope: 'user' | 'project', name: string): void {
  const suffix = ':' + encodeURIComponent('edit:' + scope + ':' + name)
  const keys = Array.from({length: storage.length}, (_, index) => storage.key(index))
  for (const key of keys) if (key?.startsWith('harness:agent-draft:') && key.endsWith(suffix)) storage.removeItem(key)
}
