type MessageCopyEvent = Pick<ClipboardEvent, 'defaultPrevented' | 'clipboardData' | 'preventDefault' | 'target'>

/** 三击选区可能使复制事件落在正文外；按当前对话的真实选区接收。 */
export function observeMessageSelectionCopy(scope: HTMLElement): () => void {
  const document = scope.ownerDocument
  const listener = (event: ClipboardEvent) => copyMessageSelection(event, scope)
  document.addEventListener('copy', listener)
  return () => document.removeEventListener('copy', listener)
}

/** 选中消息正文时只复制可见文本，避免富文本段落在粘贴处产生额外空行。 */
export function copyMessageSelection(event: MessageCopyEvent, scope: HTMLElement): void {
  if (event.defaultPrevented || !event.clipboardData) return
  const target = event.target as Element | null
  if (target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])')) return
  const document = scope.ownerDocument
  const selection = document.getSelection()
  if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return
  let range = selection.getRangeAt(0)
  const start = range.startContainer.nodeType === 1 ? range.startContainer as Element : range.startContainer.parentElement
  const body = start?.closest<HTMLElement>('[data-user-message-body]')
  if (!body || !scope.contains(body)) return
  if (!body.contains(range.endContainer)) {
    // 原生三击包含段落结束边界。只接纳正文外的空白，不裁掉其他消息、附件或控件文字。
    const content = document.createRange()
    content.selectNodeContents(body)
    const outside = range.cloneRange()
    outside.setStart(content.endContainer, content.endOffset)
    const fragment = outside.cloneContents()
    // 三击可能跨过本人消息的图标操作栏；它属于界面装饰，不属于所选消息正文。
    fragment.querySelectorAll('[data-message-actions],svg[aria-hidden="true"]').forEach(decoration => decoration.remove())
    if (fragment.textContent?.trim() || fragment.querySelector('img,video,audio,iframe,canvas,svg,input,textarea,select')) return
    range = range.cloneRange()
    range.setEnd(content.endContainer, content.endOffset)
  }
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
