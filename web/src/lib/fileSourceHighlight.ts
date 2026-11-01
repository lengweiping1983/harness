import type { FileMatch } from './fileNavigation'

/** Decorate generated syntax HTML by source offsets, including hits across tokens. */
export function markHighlightedSource(html: string, source: string, matches: FileMatch[], active: number): string {
  const root = document.createElement('span')
  // Only highlight.js output reaches this boundary; file text is escaped by it.
  root.innerHTML = html
  if (root.textContent !== source) root.textContent = source
  if (!matches.length) return root.innerHTML
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  for (let node = walker.nextNode(); node; node = walker.nextNode()) nodes.push(node as Text)
  let offset = 0, index = 0
  for (const node of nodes) {
    const value = node.data, end = offset + value.length
    while (matches[index] && matches[index]!.end <= offset) index++
    const fragment = document.createDocumentFragment()
    let position = offset, cursor = index
    while (matches[cursor] && matches[cursor]!.start < end) {
      const hit = matches[cursor]!
      const start = Math.max(offset, hit.start), stop = Math.min(end, hit.end)
      fragment.append(document.createTextNode(value.slice(position - offset, start - offset)))
      const mark = document.createElement('mark')
      mark.className = 'file-source-match'
      mark.dataset.fileMatch = String(cursor)
      if (cursor === active) mark.dataset.activeMatch = 'true'
      mark.textContent = value.slice(start - offset, stop - offset)
      fragment.append(mark)
      position = stop
      if (hit.end > end) break
      cursor++
    }
    if (position > offset) {
      fragment.append(document.createTextNode(value.slice(position - offset)))
      node.replaceWith(fragment)
    }
    offset = end
    index = cursor
  }
  return root.innerHTML
}
