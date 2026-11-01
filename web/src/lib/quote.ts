/** 在输入框光标位置插入终端摘录，保留段落间隔。 */
function leadingGap(before: string): string {
  if (!before) return ''
  if (before.endsWith('\n\n')) return ''
  // 已经以换行结尾，但只有一个 —— 再补一个形成空行。
  return before.endsWith('\n') ? '\n' : '\n\n'
}

function trailingGap(after: string): string {
  // quote 自身以 `\n` 结尾；after 再以换行开头就自然形成了空行。
  if (!after || after.startsWith('\n')) return ''
  return '\n'
}

/**
 * 把引用块插入草稿。
 *
 * - 光标在中间时插在光标处，前后用空行与已有内容隔开；
 * - 草稿为空时直接放进去，不留前导空行；
 * - 连续引用多条时每段都是独立引用块，不会并成一段。
 */
export function insertQuote(draft: string, quote: string, caret?: number): string {
  const at = typeof caret === 'number' ? Math.max(0, Math.min(caret, draft.length)) : draft.length
  const before = draft.slice(0, at)
  const after = draft.slice(at)
  return `${before}${leadingGap(before)}${quote}${trailingGap(after)}${after}`
}

/** 引用后光标应该落在哪个下标（草稿 + 引用 + 尾换行之后）。 */
export function caretAfterQuote(draft: string, quote: string, caret?: number): number {
  const at = typeof caret === 'number' ? Math.max(0, Math.min(caret, draft.length)) : draft.length
  return draft.slice(0, at).length + leadingGap(draft.slice(0, at)).length + quote.length
}
