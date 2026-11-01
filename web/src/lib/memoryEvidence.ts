import {isRecord} from './guards'
const filePrefix = 'harness:file-sources:v1\n'
const consolidationPrefix = 'harness:consolidation:v1\n'

type FileSource = {path: string; version: string}
type Input = {id: string; input_revision: number; revision: number; pinned: boolean}
export type MemoryEvidence =
  | {kind: 'files'; note: string; files: FileSource[]}
  | {kind: 'consolidation'; note: string; inputs: Input[]}

const positive = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) > 0
const exact = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))

/** 仅解释当前格式；未知或损坏结构保持原文，避免编辑时丢失依据。 */
export function parseMemoryEvidence(raw: string): MemoryEvidence | null {
  const prefix = raw.startsWith(filePrefix) ? filePrefix : raw.startsWith(consolidationPrefix) ? consolidationPrefix : null
  if (!prefix || raw.length > 8192) return null
  try {
    const value: unknown = JSON.parse(raw.slice(prefix.length))
    if (!isRecord(value) || typeof value.note !== 'string') return null
    if (prefix === filePrefix) {
      if (!exact(value, ['note', 'files']) || !Array.isArray(value.files) || value.files.length < 1 || value.files.length > 3) return null
      if (!value.files.every(file => isRecord(file) && exact(file, ['path', 'version']) && typeof file.path === 'string'
        && file.path.length > 0 && file.path.length <= 500 && typeof file.version === 'string' && /^[0-9a-f]{16}$/.test(file.version))) return null
      return {kind: 'files', note: value.note, files: value.files as FileSource[]}
    }
    if (!exact(value, ['request', 'note', 'inputs']) || typeof value.request !== 'string' || !/^[0-9a-f]{64}$/.test(value.request)
      || !Array.isArray(value.inputs) || value.inputs.length < 2 || value.inputs.length > 6) return null
    const ids = new Set<string>()
    for (const item of value.inputs) {
      if (!isRecord(item) || !exact(item, ['id', 'input_revision', 'revision', 'pinned']) || typeof item.id !== 'string'
        || item.id.length < 1 || item.id.length > 100 || ids.has(item.id) || !positive(item.input_revision)
        || !positive(item.revision) || typeof item.pinned !== 'boolean' || item.revision !== item.input_revision + Number(item.pinned)) return null
      ids.add(item.id)
    }
    return {kind: 'consolidation', note: value.note, inputs: value.inputs as Input[]}
  } catch { return null }
}

/** 只修改人类说明；保持指纹、请求身份与原始引用，未修改时逐字保留。 */
export function updateMemoryEvidenceNote(raw: string, note: string): string {
  const parsed = parseMemoryEvidence(raw)
  if (!parsed) return note
  if (parsed.note === note) return raw
  const prefix = parsed.kind === 'files' ? filePrefix : consolidationPrefix
  return prefix + JSON.stringify({...JSON.parse(raw.slice(prefix.length)), note})
}
