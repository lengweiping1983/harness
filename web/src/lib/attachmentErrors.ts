import type {ResourceCopy} from '@/types'

const rules: Record<string, Record<string, readonly string[]>> = {
  'attachment/bad-payload': {'ui.attachments.server.dataRequired': [], 'ui.attachments.server.entryObject': ['p0'], 'ui.attachments.server.invalidPayload': []},
  'attachment/stats-unavailable': {'ui.attachments.server.statsFailed': []},
  'attachment/bad-base64': {'ui.attachments.server.base64Length': [], 'ui.attachments.server.base64Decode': ['p0']},
  'attachment/non-canonical-base64': {'ui.attachments.server.base64Canonical': []},
  'attachment/no-files': {'ui.attachments.server.filesRequired': []},
  'attachment/missing-name': {'ui.attachments.server.nameRequired': ['p0']},
  'attachment/request-too-large': {'ui.attachments.server.requestTooLarge': []},
  'attachment/not-found': {'ui.attachments.server.notFound': []},
  'attachment/invalid': {'ui.attachments.server.bytesRequired': []},
  'attachment/empty': {'ui.attachments.server.empty': []},
  'attachment/too-large': {'ui.attachments.server.fileTooLarge': ['p0', 'p1']},
  'attachment/type-denied': {'ui.attachments.server.extensionDenied': ['p0'], 'ui.attachments.server.mediaDenied': ['p0']},
  'attachment/unsupported': {'ui.attachments.server.unsupported': ['p0']},
  'attachment/too-many': {'ui.attachments.server.tooMany': ['p0', 'p1']},
  'attachment/batch-too-large': {'ui.attachments.server.batchTooLarge': ['p0']},
}

/** undefined表示未登记错误，null表示已登记产品回执资源损坏；不从正文推断参数。 */
export function attachmentErrorCopy(code: string, details: unknown): ResourceCopy | null | undefined {
  if (!Object.hasOwn(rules, code)) return undefined
  if (!details || typeof details !== 'object' || Array.isArray(details)) return null
  const copy = (details as Record<string, unknown>).copy
  if (!copy || typeof copy !== 'object' || Array.isArray(copy)) return null
  const {key, params = {}} = copy as Record<string, unknown>
  const keys = rules[code]!
  if (typeof key !== 'string' || !Object.hasOwn(keys, key) || !params || typeof params !== 'object' || Array.isArray(params)) return null
  const entries = Object.entries(params), expected = keys[key]!
  if (entries.length !== expected.length || entries.some(([name, value]) => !expected.includes(name) ||
    typeof value !== 'string' && (typeof value !== 'number' || !Number.isFinite(value)))) return null
  return {key, params: Object.fromEntries(entries) as Record<string, string | number>}
}
