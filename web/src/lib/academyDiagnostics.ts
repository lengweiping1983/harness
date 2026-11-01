export type AcademySourceLocation = {path: string; line: number}
export type AcademyDiagnostic = {summary: string; location?: AcademySourceLocation}

/** 只投影标准 Python 错误尾行；原始诊断始终由输出组件保留。 */
export function academyDiagnostic(stderr: string, root: string, files: readonly string[]): AcademyDiagnostic | null {
  const lines = stderr.split(/\r\n|\n|\r/)
  const frames = lines.flatMap((line, index) => {
    const match = /^\s*File "([^"\r\n]+)", line ([1-9]\d*)(?:, in .*)?$/.exec(line)
    const number = match ? Number(match[2]) : NaN
    return match && Number.isSafeInteger(number) ? [{file: match[1], line: number, index}] : []
  })
  if (!frames.length) return null
  const summary = lines.slice(frames.at(-1)!.index + 1).find(line => /^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?::(?: .*)?)?$/.test(line))
  if (!summary) return null
  const normalize = (value: string) => value.replaceAll('\\', '/').replace(/\/+$/, '')
  const base = normalize(root)
  const windows = /^[A-Za-z]:\//.test(base) || base.startsWith('//')
  const compare = (value: string) => windows ? value.toLowerCase() : value
  let location: AcademySourceLocation | undefined
  for (const frame of frames) {
    const full = normalize(frame.file)
    if (!base || !compare(full).startsWith(compare(base + '/'))) continue
    const relative = full.slice(base.length + 1)
    if (relative.split('/').some(part => !part || part === '.' || part === '..')) continue
    const path = files.find(file => compare(file) === compare(relative))
    if (path) location = {path, line: frame.line}
  }
  return {summary, ...(location ? {location} : {})}
}

/** textarea 使用 UTF-16 偏移；保留源码原换行并拒绝越界行。 */
export function academyLineRange(content: string, line: number): [number, number] | null {
  if (!Number.isSafeInteger(line) || line < 1) return null
  let start = 0
  const endings = content.matchAll(/\r\n|\r|\n/g)
  for (let number = 1; number < line; number++) {
    const ending = endings.next()
    if (ending.done) return null
    start = ending.value.index + ending.value[0].length
  }
  const next = /\r\n|\r|\n/.exec(content.slice(start))
  return [start, next ? start + next.index : content.length]
}
