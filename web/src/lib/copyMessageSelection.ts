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
  const text = selection.toString()
  if (!text) return
  event.clipboardData.clearData()
  event.clipboardData.setData('text/plain', text)
  event.preventDefault()
}
