import type {ClipboardEvent} from 'react'

/** 选中消息正文时只复制可见文本，避免富文本段落在粘贴处产生额外空行。 */
export function copyMessageSelection(event: ClipboardEvent<HTMLElement>): void {
  if (event.defaultPrevented) return
  const body = event.currentTarget
  const selection = body.ownerDocument.getSelection()
  if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return
  const range = selection.getRangeAt(0)
  // 跨消息、附件或编辑器的选择继续交给浏览器，不截断用户选择。
  if (!body.contains(range.startContainer) || !body.contains(range.endContainer)) return
  let text = selection.toString()
  if (!text) return
  // 可见选区会把块布局转换成换行；纯文本剪贴板也必须排除额外的尾部换行。
  // Range 只读取实际选中的文本节点，保留其中已有的空行，不用 trimEnd 删除原文空白。
  const tail = text.match(/[\r\n][ \t\r\n]*$/)?.[0]
  if (tail) {
    const sourceTail = range.toString().match(/[\r\n][ \t\r\n]*$/)?.[0] ?? ''
    const breakCount = (value: string) => value.match(/\r\n|\r|\n/g)?.length ?? 0
    if (breakCount(tail) > breakCount(sourceTail)) text = text.slice(0, -tail.length) + sourceTail
  }
  event.clipboardData.clearData()
  event.clipboardData.setData('text/plain', text)
  event.preventDefault()
}
