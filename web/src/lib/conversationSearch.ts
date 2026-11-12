import type {ConversationSearchResult} from '@/types'

/** 服务器按Python字符串空白规则归一化查询，不能按大小写猜测命中正文。 */
function serverQuery(query: string) {
  return query.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/g, '')
}

/** 合入前验证请求查询、水位及降序分页，损坏回执不改变已有列表。 */
export function validSearchResult(value: ConversationSearchResult, sessionId: string, query: string,
  through?: number, before?: number, total?: number): boolean {
  if (!value || typeof value !== 'object' || value.session_id !== sessionId || value.query !== serverQuery(query)
    || !Number.isSafeInteger(value.through) || value.through < -1
    || (through !== undefined && value.through !== through)
    || !Number.isSafeInteger(value.total) || value.total < 0 || (total !== undefined && total !== value.total)
    || !Array.isArray(value.results) || value.results.length > 54 || value.total < value.results.length
    || typeof value.has_more !== 'boolean') return false
  let previous = before ?? value.through + 1
  for (const hit of value.results) {
    if (!hit || typeof hit !== 'object' || !Number.isSafeInteger(hit.source_seq) || hit.source_seq < 0
      || hit.source_seq > value.through || hit.source_seq >= previous
      || !Number.isFinite(hit.source_time) || !['user', 'assistant'].includes(hit.role)
      || typeof hit.prefix !== 'string' || typeof hit.match !== 'string' || !hit.match
      || typeof hit.suffix !== 'string' || !Number.isSafeInteger(hit.start_char) || hit.start_char < 0) return false
    previous = hit.source_seq
  }
  return value.has_more ? value.results.length > 0 && value.next_before === previous
    && value.total > value.results.length : value.next_before === null
}
