import type { ToolOutputPreview } from '@/types'

const LIMIT = 32768
/** 偏移以 Unicode 码点计数，与 Python 字符索引一致（包含 emoji）。 */
export function mergeToolOutput(previous: ToolOutputPreview | undefined, raw: Record<string, unknown>): ToolOutputPreview | undefined {
  const seq = raw.output_seq
  if (typeof seq !== 'number' || !Number.isSafeInteger(seq) || seq < 1 || seq <= (previous?.seq ?? 0)) return previous
  const read = (name: 'stdout' | 'stderr') => {
    const offset = raw[name + '_offset']
    if (typeof offset !== 'number' || !Number.isSafeInteger(offset) || offset < 0 || typeof raw[name] !== 'string') return null
    const delta = Array.from(raw[name] as string)
    const end = offset + delta.length
    if (!Number.isSafeInteger(end)) return null
    const oldEnd = previous?.[name === 'stdout' ? 'stdoutEnd' : 'stderrEnd'] ?? 0
    const old = offset > oldEnd ? [] : Array.from(previous?.[name] ?? '')
    const tail = old.concat(delta.slice(Math.max(0, oldEnd - offset))).slice(-LIMIT)
    return { text: tail.join(''), end: Math.max(oldEnd, end), dropped: Math.max(oldEnd, end) - tail.length }
  }
  const out = read('stdout'), err = read('stderr')
  if (!out || !err) return previous
  const encodingErrors = Array.isArray(raw.encoding_errors) ? raw.encoding_errors.filter((issue): issue is import('@/lib/protocol.generated').EncodingError =>
    issue && typeof issue === 'object' && typeof issue.channel === 'string' && typeof issue.encoding === 'string' && typeof issue.reason === 'string' && Number.isSafeInteger(issue.offset) && issue.offset >= 0) : undefined
  return { ...(encodingErrors?.length ? {encodingErrors} : {}), seq, stdout: out.text, stderr: err.text, stdoutEnd: out.end, stderrEnd: err.end, stdoutDropped: out.dropped, stderrDropped: err.dropped }
}
