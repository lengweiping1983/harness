export interface FileLineRange { start: number; end?: number }
export interface FileMatch { start: number; end: number; line: number; endLine: number }

export function validLineRange(start: number, end?: number): FileLineRange | undefined {
  if (!Number.isSafeInteger(start) || start < 1 || end !== undefined && (!Number.isSafeInteger(end) || end < start)) return undefined
  return { start, ...(end !== undefined ? { end } : {}) }
}

export function lineStarts(source: string): number[] {
  const starts = [0]
  for (let index = 0; index < source.length; index++) if (source[index] === '\n') starts.push(index + 1)
  return starts
}

/** One-based line lookup without rescanning the file for every search result. */
export function lineAt(starts: number[], offset: number): number {
  let low = 0, high = starts.length
  while (low + 1 < high) {
    const middle = (low + high) >>> 1
    if (starts[middle]! <= offset) low = middle
    else high = middle
  }
  return low + 1
}

export function parseLineSelection(value: string, total: number): FileLineRange | undefined {
  const match = /^(\d+)(?:\s*-\s*(\d+))?$/.exec(value.trim())
  if (!match) return undefined
  const range = validLineRange(Number(match[1]), match[2] === undefined ? undefined : Number(match[2]))
  return range && (range.end ?? range.start) <= total ? range : undefined
}

/** Literal, case-insensitive Unicode search. Offsets remain in the original text. */
export function findFileMatches(source: string, query: string, starts: number[], limit = 1000): { matches: FileMatch[]; capped: boolean } {
  if (!query) return { matches: [], capped: false }
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const pattern = new RegExp(escaped, 'giu')
  const matches: FileMatch[] = []
  for (let hit = pattern.exec(source); hit; hit = pattern.exec(source)) {
    if (matches.length >= limit) return { matches, capped: true }
    matches.push({ start: hit.index, end: hit.index + hit[0].length,
      line: lineAt(starts, hit.index), endLine: lineAt(starts, hit.index + hit[0].length - 1) })
  }
  return { matches, capped: false }
}
