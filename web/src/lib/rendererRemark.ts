import { isRendererInfo } from './streaming'

interface CodeNode {
  type?: string
  lang?: string | null
  meta?: string | null
  data?: Record<string, unknown>
  position?: { start: { offset?: number }; end: { offset?: number } }
  children?: CodeNode[]
}

/** 从实际围栏位置核对闭合，不用整条消息的流式状态推测嵌套块状态。 */
function closedFence(node: CodeNode, source: string): boolean {
  const start = node.position?.start.offset
  const end = node.position?.end.offset
  if (start === undefined || end === undefined) return false
  const block = source.slice(start, end)
  const opening = /^(`{3,}|~{3,})/.exec(block)?.[1]
  const newline = block.lastIndexOf('\n')
  if (!opening || newline < 0) return false
  // 引用前缀属于容器；列表围栏的续行只保留缩进。
  const closing = /^(?:[ \t]*>[ \t]?)*[ \t]*(`{3,}|~{3,})[ \t]*\r?$/.exec(block.slice(newline + 1))?.[1]
  return Boolean(closing && closing[0] === opening[0] && closing.length >= opening.length)
}

/** 将围栏身份和闭合状态传过 remark → 清洗 → React 组件，保留列表及引用结构。 */
export function remarkRenderer() {
  return (tree: unknown, file?: { value?: unknown }) => {
    const source = typeof file?.value === 'string' ? file.value : ''
    const walk = (node: CodeNode): void => {
      if (node.type === 'code') {
        const props: Record<string, unknown> = {
          ...node.data?.hProperties as Record<string, unknown> | undefined,
          'data-harness-closed': String(closedFence(node, source)),
        }
        if (isRendererInfo(`${node.lang ?? ''} ${node.meta ?? ''}`)) props['data-harness-renderer'] = 'true'
        node.data = { ...node.data, hProperties: props }
      }
      node.children?.forEach(walk)
    }
    walk(tree as CodeNode)
  }
}
