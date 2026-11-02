import { memo, useMemo } from 'react'
import { Activity } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { cn } from '@/lib/utils'
import { WidgetCard } from '@/components/chat/blocks/WidgetCard'
import { CodeBlock } from './CodeBlock'

/** 完整渲染块复用工具卡片；未闭合源码绝不交给脚本沙箱。 */
export const RendererFrame = memo(function RendererFrame({
  code, closed = true, streaming = false, className,
}: {
  code: string
  closed?: boolean
  streaming?: boolean
  className?: string
}) {
  const locale = useLocale()
  const payload = useMemo(() => ({
    kind: 'html' as const, title: t('ui.renderer.title'), code, chars: code.length, loadingMessages: [],
  }), [code, locale])
  if (!closed && streaming) return (
    <figure aria-busy="true" className={cn('renderer-frame overflow-hidden rounded-xl border border-line bg-raised/40', className)}>
      <figcaption role="status" className="flex h-8 items-center gap-2 border-b border-line px-3 text-xs text-muted">
        <Activity className="h-3 w-3" />{t('ui.renderer.rendering')}
      </figcaption>
      <div className="grid min-h-24 place-items-center p-4"><div className="shimmer-bar h-40 w-full max-w-lg rounded-lg" /></div>
    </figure>
  )
  if (!closed) return (
    <div className={cn('renderer-frame', className)}>
      <p role="note" className="text-xs text-warn">{t('ui.renderer.incomplete')}</p>
      <CodeBlock code={code} language="html" previewReady={false} />
    </div>
  )
  return <div className={cn('renderer-frame', className)}><WidgetCard payload={payload} /></div>
})
