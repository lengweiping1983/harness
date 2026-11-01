/** Display-only repair for provider reasoning filled with token-sized lines. */
export function formatThinkingText(raw: string): string {
  const text = raw.replace(/\r\n?/g, '\n')
  const lines = text.split('\n')
  let fence: { char: string; size: number } | null = null
  const rows = lines.map((line) => {
    const marker = /^ {0,3}(`{3,}|~{3,})/.exec(line)?.[1]
    if (fence) {
      if (marker && marker[0] === fence.char && marker.length >= fence.size && line.trim() === marker) fence = null
      return { line, protected: true }
    }
    if (marker) {
      fence = { char: marker[0]!, size: marker.length }
      return { line, protected: true }
    }
    return { line, protected: /^(?: {4}|\t| {0,3}(?:#{1,6}\s|\d+[.)]\s|[-+*]\s|>|\||[-*_]{3,}\s*$))/.test(line) }
  })
  const prose = rows.filter((row) => !row.protected && row.line.trim())
  const fragmented = prose.length >= 6
    && prose.filter((row) => row.line.trim().length <= 24).length / prose.length >= .8
    && prose.reduce((sum, row) => sum + row.line.trim().length, 0) / prose.length <= 16
  const excessiveBlanks = /\n(?:[ \t]*\n){3,}/.test(text)
  const proseChars = rows.filter((row) => !row.protected).map((row) => row.line).join('\n')
  const blankFlood = excessiveBlanks && (proseChars.match(/\n/g)?.length ?? 0) / Math.max(1, proseChars.length) > .5
  // Some providers separate each tiny fragment with a blank line. Six-line
  // detection misses early streaming output, and treating every blank as a
  // paragraph boundary keeps the fragmented layout even after repair.
  const sparseRows = new Set<number>()
  for (let start = 0; start < rows.length;) {
    if (rows[start]!.protected) { start++; continue }
    let end = start
    while (end < rows.length && !rows[end]!.protected) end++
    const paragraphs = rows.slice(start, end).map((row) => row.line).join('\n').trim().split(/\n[ \t]*\n+/)
    if (paragraphs.length >= 3
      && paragraphs.every((part) => !part.includes('\n') && part.trim().length <= 24
        && !/[.!?。！？][”’"')）\]]*$/.test(part.trim()))
      && paragraphs.reduce((sum, part) => sum + part.trim().length, 0) / paragraphs.length <= 16) {
      for (let index = start; index < end; index++) sparseRows.add(index)
    }
    start = end
  }
  if (!fragmented && !excessiveBlanks && !sparseRows.size) return raw

  const output: string[] = []
  let paragraph = ''
  const flush = () => { if (paragraph) output.push(paragraph); paragraph = '' }
  for (const [index, row] of rows.entries()) {
    if (row.protected) { flush(); output.push(row.line); continue }
    const line = row.line.trim()
    if (!line) {
      if (blankFlood || sparseRows.has(index)) continue
      flush()
      if (output.length && output.at(-1) !== '') output.push('')
      continue
    }
    if (fragmented || blankFlood || sparseRows.has(index)) {
      // Join contractions/punctuation and adjacent CJK without inventing words.
      const noSpace = !paragraph || /^[,.;:!?，。；：！？、）\])]/.test(line)
        || /^['’](?:s|t|re|ve|ll|d|m)\b/i.test(line)
        || /[(（\[]$/.test(paragraph)
        || /[\u3400-\u9fff]$/.test(paragraph) && /^[\u3400-\u9fff（“‘]/.test(line)
      paragraph += (noSpace ? '' : ' ') + line
    } else { flush(); output.push(row.line) }
  }
  flush()
  while (output.at(-1) === '') output.pop()
  return output.join('\n')
}
