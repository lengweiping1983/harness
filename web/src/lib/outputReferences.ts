import { guessKind, isTextFile } from './fileLinks'

interface ReferenceNode {
  type: string
  value?: string
  url?: string
  children?: ReferenceNode[]
}

/** Conservative recognition: an entire reference, never a shell command or code expression. */
export function outputReferenceTarget(value: string): string | undefined {
  if (!value || value !== value.trim() || /[\n\r<>|"`]/.test(value)) return undefined
  if (/^https?:\/\//i.test(value)) {
    if (/\s/.test(value)) return undefined
    try { new URL(value); return value } catch { return undefined }
  }
  const windows = /^[A-Za-z]:[\\/]/.test(value)
  if (!windows && (/\s/.test(value) || /^[a-z][a-z0-9+.-]*:/i.test(value))) return undefined
  if (/[;&=]/.test(value) || value.startsWith('//') || value.startsWith('\\\\')) return undefined
  const path = value.replace(/#L\d+(?:-L?\d+)?$/, '').replace(/:\d+$/, '')
  if (path.includes('#') || path.includes('?') || /:(?![\\/])/.test(path)) return undefined
  // Bare property names like object.value are not references. Unknown extensions
  // require a directory; familiar source/media filenames can stand alone.
  const file = path.split(/[\\/]/).pop() ?? ''
  if (isTextFile(file) || guessKind(file) !== 'file') return value
  if (/[\\/]/.test(path) && /\.[A-Za-z0-9]{1,10}$/.test(file)) return value
  return undefined
}

const SKIP = new Set(['link', 'linkReference', 'image', 'imageReference', 'definition', 'code', 'math', 'inlineMath', 'html'])

function link(value: string, child: ReferenceNode): ReferenceNode {
  return { type: 'link', url: value, children: [child] }
}

function textReferences(value: string): ReferenceNode[] {
  // Whitespace delimits prose references. Paths containing spaces must be explicit
  // links or a single inline-code span so the filename boundary is unambiguous.
  const pattern = /(^|[\s（(“"'：:])((?:[A-Za-z]:[\\/]|\/(?!\/)|\.{1,2}[\\/]|[A-Za-z0-9_.-]+[\\/])[^\s<>"'`，。；！？（）()]+)/g
  const result: ReferenceNode[] = []
  let offset = 0
  for (const match of value.matchAll(pattern)) {
    const start = match.index! + match[1]!.length
    const candidate = match[2]!.replace(/[.,，。;；!?！？）)]+$/, '')
    const target = outputReferenceTarget(candidate)
    if (!target) continue
    if (start > offset) result.push({ type: 'text', value: value.slice(offset, start) })
    result.push(link(target, { type: 'text', value: candidate }))
    offset = start + candidate.length
  }
  if (!offset) return [{ type: 'text', value }]
  if (offset < value.length) result.push({ type: 'text', value: value.slice(offset) })
  return result
}

/** Transform Markdown nodes, preserving existing links and all code blocks. */
export function remarkOutputReferences() {
  return (tree: ReferenceNode): void => {
    const visit = (node: ReferenceNode): void => {
      if (SKIP.has(node.type) || !node.children) return
      node.children = node.children.flatMap((child) => {
        if (child.type === 'inlineCode') {
          const target = outputReferenceTarget(child.value ?? '')
          return target ? [link(target, child)] : [child]
        }
        if (child.type === 'text') return textReferences(child.value ?? '')
        visit(child)
        return [child]
      })
    }
    visit(tree)
  }
}
