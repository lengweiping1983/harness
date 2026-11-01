import { scanFences } from './streaming'

export interface TeamReport {
  status: string
  checks: { name: string; passed: boolean; detail: string }[]
  evidence: string[]
}

/** 这是成员声明的展示模型，不能据此推断工具证据已核验。 */
export function parseTeamReport(code: string): TeamReport | null {
  if (code.length > 160_000) return null
  try {
    const value = JSON.parse(code)
    if (!value || typeof value !== 'object' || Array.isArray(value)
      || typeof value.status !== 'string'
      || !['ok', 'pass', 'passed', 'success', 'succeeded', 'done', 'completed', 'fail', 'failed', 'failure', 'error', 'blocked', 'rejected'].includes(value.status.trim().toLowerCase())
      || !Array.isArray(value.checks) || !value.checks.length || value.checks.length > 32
      || value.checks.some((row: unknown) => {
        if (!row || typeof row !== 'object' || Array.isArray(row)) return true
        const check = row as Record<string, unknown>
        return typeof check.name !== 'string' || !check.name.trim() || check.name.length > 200
          || typeof check.passed !== 'boolean' || typeof check.detail !== 'string' || check.detail.length > 4000
      })
      || (value.evidence !== undefined && (!Array.isArray(value.evidence) || value.evidence.length > 32
        || value.evidence.some((item: unknown) => typeof item !== 'string' || item.length > 4000)))) return null
    return { status: value.status, checks: value.checks, evidence: value.evidence ?? [] }
  } catch {
    return null
  }
}

export type TeamOutputPart = { kind: 'markdown'; text: string } | { kind: 'report'; code: string; report: TeamReport }

/** 只转换顶层、完整闭合的 JSON 围栏，其余 Markdown 保持原始切片。 */
export function splitTeamOutput(text: string): TeamOutputPart[] {
  const parts: TeamOutputPart[] = []
  let cursor = 0
  for (const fence of scanFences(text)) {
    if (!fence.closed || fence.indent || !['json', ''].includes(fence.lang)) continue
    const report = parseTeamReport(fence.code)
    if (!report) continue
    if (fence.start > cursor) parts.push({ kind: 'markdown', text: text.slice(cursor, fence.start) })
    parts.push({ kind: 'report', code: fence.code, report })
    cursor = fence.end
  }
  if (cursor < text.length) parts.push({ kind: 'markdown', text: text.slice(cursor) })
  return parts
}
