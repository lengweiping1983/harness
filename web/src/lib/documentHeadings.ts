interface HeadingNode {
  type: string
  value?: string
  children?: HeadingNode[]
  data?: { hProperties?: Record<string, unknown>; [key: string]: unknown }
}

export function headingSlug(value: string): string {
  return value.toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, '').trim().replace(/\s+/g, '-')
}

function headingText(node: HeadingNode): string {
  return node.value ?? node.children?.map(headingText).join('') ?? ''
}

/** File documents are parsed as one tree so repeated headings get stable unique IDs. */
export function remarkDocumentHeadings({ prefix }: { prefix: string }) {
  return (tree: HeadingNode): void => {
    const used = new Set<string>()
    const visit = (node: HeadingNode): void => {
      if (node.type === 'heading') {
        const base = headingSlug(headingText(node))
        let slug = base, index = 1
        while (used.has(slug)) slug = `${base}-${index++}`
        used.add(slug)
        node.data = { ...node.data, hProperties: { ...node.data?.hProperties, id: prefix + slug } }
      }
      node.children?.forEach(visit)
    }
    visit(tree)
  }
}
