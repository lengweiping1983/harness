import type {PersonalityProfile} from './protocol.generated'

export type PersonalityDraft = {
  name: string
  description: string
  prompt: string
  code: string | null
  editing: { id: string; revision: string; name: string } | null
}

const codes: Record<NonNullable<PersonalityProfile['code']>, true> = {
  'systems-planner': true, 'research-analyst': true, 'long-term-adviser': true, 'creative-partner': true,
  'reliable-executor': true, 'troubleshooter': true, 'detail-steward': true, 'experience-designer': true,
  'delivery-lead': true, 'solution-explorer': true, 'collaboration-coach': true, 'idea-catalyst': true,
  'delivery-manager': true, 'action-tester': true, 'team-supporter': true, 'demo-partner': true,
}

const PREFIX = 'harness:personality-draft:library:'

export function personalityDraftKey(target: string): string {
  return PREFIX + encodeURIComponent(target)
}

/** 各目标独立保存；读取失败或某条损坏不能清理其他草稿。 */
export function listPersonalityDrafts(storage: Pick<Storage, 'length' | 'key' | 'getItem'>): Map<string, PersonalityDraft> {
  const drafts = new Map<string, PersonalityDraft>()
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index)
    if (!key?.startsWith(PREFIX)) continue
    const draft = readPersonalityDraft(storage.getItem(key))
    if (draft) drafts.set(key!, draft)
  }
  return drafts
}

export function readPersonalityDraft(raw: string | null): PersonalityDraft | null {
  if (!raw) return null
  try {
    const value = JSON.parse(raw)
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null
    if (typeof value.name !== 'string' || value.name.length > 120 || typeof value.description !== 'string' || value.description.length > 500 || typeof value.prompt !== 'string' || value.prompt.length > 1000) return null
    if (value.code !== null && (typeof value.code !== 'string' || !Object.hasOwn(codes, value.code))) return null
    if (/<\/?agent_personality\b/.test(value.prompt)) return null
    const row = value.editing
    if (row !== null && (!row || typeof row.id !== 'string' || !/^[a-f0-9]{32}$/.test(row.id) || typeof row.revision !== 'string' || !/^[a-f0-9]{64}$/.test(row.revision) || typeof row.name !== 'string' || row.name.length > 120)) return null
    return { name: value.name, description: value.description, prompt: value.prompt.trim(), code: value.code, editing: row ? { id: row.id, revision: row.revision, name: row.name } : null }
  } catch { return null }
}

/** 同键原件损坏时只保留内存编辑，不覆盖或删除浏览器中的原字节。 */
export function persistPersonalityDraft(storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>, key: string, value: PersonalityDraft | null): boolean {
  try {
    const original = storage.getItem(key)
    if (original !== null && !readPersonalityDraft(original)) return false
    if (value === null) storage.removeItem(key)
    else {
      const serialized = JSON.stringify(value)
      if (!readPersonalityDraft(serialized)) return false
      storage.setItem(key, serialized)
    }
    return true
  } catch { return false }
}
