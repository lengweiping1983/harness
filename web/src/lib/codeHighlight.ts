import {highlightToHtml} from './highlight'

/** 高频输出只在短暂停顿后着色，超大代码保留原文以避免同步着色阻塞交互。 */
const MAX_HIGHLIGHT_CHARS = 100_000
const HIGHLIGHT_SETTLE_MS = 80

export function scheduleCodeHighlight(code: string, lang: string, publish: (html: string | null) => void): () => void {
  if (code.length > MAX_HIGHLIGHT_CHARS) {
    publish(null)
    return () => {}
  }
  let cancelled = false
  const timer = setTimeout(() => {
    void highlightToHtml(code, lang).then(html => {
      if (!cancelled) publish(html)
    }, () => {
      if (!cancelled) publish(null)
    })
  }, HIGHLIGHT_SETTLE_MS)
  return () => {cancelled = true; clearTimeout(timer)}
}
