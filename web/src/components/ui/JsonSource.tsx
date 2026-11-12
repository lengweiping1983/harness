import { useEffect, useState } from 'react'
import { scheduleCodeHighlight } from '@/lib/codeHighlight'
import { cn } from '@/lib/utils'

/** 只读 JSON 共用展示：保留原文，异步着色失败或内容过大时直接回退。 */
export function JsonSource({ text, active = true, className, label }: { text: string; active?: boolean; className?: string; label?: string }) {
  const [syntax, setSyntax] = useState<{ text: string; html: string | null } | null>(null)
  useEffect(() => {
    if (!active) return
    return scheduleCodeHighlight(text, 'json', html => setSyntax({ text, html }))
  }, [text, active])
  const html = syntax?.text === text ? syntax.html : null
  return <pre tabIndex={0} aria-label={label} data-source-language="json" data-json-source className={cn('file-preview-source json-source max-h-80 min-w-0 overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] rounded-lg border border-line bg-raised/35 p-3 font-mono text-xs leading-code text-ink', className)}>{html === null ? text : <span data-json-syntax dangerouslySetInnerHTML={{ __html: html }} />}</pre>
}
