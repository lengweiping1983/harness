import {SpecChart} from '@/components/markdown/SpecChart'
import {DataTable} from '@/components/markdown/DataTable'
import {isTableKind} from '@/lib/tableData'
import {isChartKind} from '@/lib/chartSpec'
import { usePageVisible } from '@/hooks/usePageVisible'
import { useDialogFocus } from '@/hooks/useDialogFocus'
import { t, useLocale } from '@/i18n'
import { memo, useEffect, useMemo, useRef, useState, useId } from 'react'
import { Check, Code, Copy, Download, Eye, Image, ImageDown, Maximize2, Minimize2 } from 'lucide-react'
import { cn, copyText } from '@/lib/utils'
import { IconButton, Spinner } from '@/components/ui/primitives'
import { HtmlSandboxFrame } from '@/components/ui/HtmlSandboxFrame'
import {SvgImage} from '@/components/ui/SvgImage'
import { useAppStore } from '@/store/app'
import { parseProgressiveSvg, parseSvgImage, scopeSvg } from '@/lib/progressiveSvg'
import { widgetFileName, type WidgetPayload, type WidgetPreview } from '@/lib/widgetPayload'

/**
 * 对话内小组件卡片：show_widget 工具的渲染端。
 *
 * 两种载体两条渲染路径，安全边界各不相同：
 * - SVG：复用 progressiveSvg 的清洗管线（剥 script/事件属性/危险 URL）
 *   后静态图内联；样式／动画图与 ```svg 围栏共用隔离图片上下文；
 * - HTML：共享的 HtmlSandboxFrame（opaque origin 沙箱 + 主题注入 + 高度回传）。
 *
 * 图片导出只对 SVG 开放：HTML 在 opaque origin 沙箱里，宿主读不到内容，
 * 这是沙箱隔离的直接代价，不绕。
 */

/** 导出呈现时的颜色与字体，避免 currentColor／主题变量在图片里失效。 */
function svgSnapshot(node: SVGSVGElement | null, fallback: string): string {
  if (!node) return fallback
  const clone = node.cloneNode(true) as SVGSVGElement
  const originals = [node, ...node.querySelectorAll('*')]
  const copies = [clone, ...clone.querySelectorAll('*')]
  const properties = ['color', 'fill', 'stroke', 'stroke-width', 'fill-opacity', 'stroke-opacity', 'opacity', 'font-family', 'font-size', 'font-weight', 'text-anchor', 'stop-color', 'stop-opacity']
  originals.forEach((element, index) => {
    const style = getComputedStyle(element)
    const copy = copies[index] as SVGElement
    for (const property of properties) {
      const value = style.getPropertyValue(property).replace(/url\(["']?[^)]*?#([^)"']+)["']?\)/g, 'url(#$1)')
      if (value) copy.style.setProperty(property, value)
    }
  })
  return new XMLSerializer().serializeToString(clone)
}

/** SVG → PNG Blob：viewBox 决定像素尺寸，2x 输出，背景跟随当前主题。 */
async function svgToPngBlob(svgText: string, background: string): Promise<Blob> {
  const withNs = /xmlns=/.test(svgText) ? svgText : svgText.replace('<svg', '<svg xmlns="http://www.w3.org/2000/svg"')
  const viewBox = /viewBox\s*=\s*"([^"]+)"/i.exec(withNs)?.[1]?.trim().split(/[\s,]+/).map(Number)
  const width = Math.max(1, Math.round(viewBox?.[2] || 680))
  const height = Math.max(1, Math.round(viewBox?.[3] || 400))
  const url = URL.createObjectURL(new Blob([withNs], { type: 'image/svg+xml;charset=utf-8' }))
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const node = new window.Image()
      node.onload = () => resolve(node)
      node.onerror = () => reject(new Error('svg decode failed'))
      node.src = url
    })
    const scale = 2
    const canvas = document.createElement('canvas')
    canvas.width = width * scale
    canvas.height = height * scale
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('canvas unavailable')
    ctx.fillStyle = background
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height)
    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('png encode failed'))), 'image/png')
    })
  } finally {
    URL.revokeObjectURL(url)
  }
}

function downloadBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

const WidgetContent = memo(function WidgetContent({ payload, previewCode, active = true }: { payload: WidgetPayload; previewCode?: string; active?: boolean }) {
  useLocale()
  const settingsOpen = useAppStore(state => state.settingsOpen)
  const scope = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const [showCode, setShowCode] = useState(false)
  const [copied, setCopied] = useState(false)
  const [zoom, setZoom] = useState(false)
  const figureRef = useRef<HTMLElement | null>(null)
  useDialogFocus(figureRef, zoom, () => setZoom(false))
  const copyTimer = useRef<ReturnType<typeof setTimeout>>()
  useEffect(() => () => copyTimer.current && clearTimeout(copyTimer.current), [])

  const parsed = useMemo(
    () => (payload.kind === 'svg' ? scopeSvg(parseProgressiveSvg(payload.code), scope) : null),
    [payload.kind, payload.code, scope],
  )
  const image = useMemo(() => payload.kind === 'svg' ? parseSvgImage(payload.code) : null, [payload.kind, payload.code])
  const svgText = image?.markup ?? (parsed?.root ? parsed.markup : null)

  const notifyExportFailure = () => useAppStore.getState().toast({
    title: t('ui.widget.exportFailed'),
    description: t('ui.widget.exportFailedHint'),
    tone: 'danger',
  })

  const copyCode = async () => {
    if (copyTimer.current) clearTimeout(copyTimer.current)
    const ok = await copyText(payload.code)
    setCopied(ok)
    if (ok) copyTimer.current = setTimeout(() => setCopied(false), 1600)
  }
  const downloadCode = () => downloadBlob(new Blob([payload.kind === 'svg' ? svgText ?? payload.code : payload.code], { type: payload.kind === 'svg' ? 'image/svg+xml;charset=utf-8' : isChartKind(payload.kind) ? 'application/json;charset=utf-8' : 'text/html;charset=utf-8' }), widgetFileName(payload))
  const exportPng = async (target: 'clipboard' | 'file') => {
    if (!svgText) return
    try {
      const background = getComputedStyle(document.body).backgroundColor
        || (document.documentElement.classList.contains('dark') ? '#181c1e' : '#f6f7f8')
      const blob = await svgToPngBlob(svgSnapshot(figureRef.current?.querySelector('.widget-host > svg') as SVGSVGElement | null, svgText), background)
      if (target === 'file') {
        downloadBlob(blob, widgetFileName(payload).replace(/\.svg$/, '.png'))
        return
      }
      if (typeof ClipboardItem === 'undefined' || !navigator.clipboard?.write) {
        notifyExportFailure()
        return
      }
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
    } catch {
      notifyExportFailure()
    }
  }

  const header = (
    <figcaption className="flex h-9 items-center gap-2 border-b border-line bg-raised/60 px-3">
      <span className="min-w-0 flex-1 truncate text-compact font-medium text-ink" title={payload.title}>{payload.title}</span>
      <span className="shrink-0 rounded-md bg-brand/[0.12] px-1.5 py-0.5 font-mono text-2xs font-medium uppercase tracking-wide text-brand">{payload.kind}</span>
      {parsed?.repaired ? (
        <span className="shrink-0 rounded-md bg-warn/[0.12] px-1.5 py-0.5 text-2xs font-medium text-warn">{t("ui.components.markdown.LiveChart.1f06a566db54")}</span>
      ) : null}
      <div className="ml-auto flex shrink-0 items-center gap-0.5">
        <IconButton label={showCode ? t('ui.widget.viewWidget') : t('ui.widget.viewCode')} size="xs" active={showCode} onClick={() => setShowCode((v) => !v)}>
          {showCode ? <Eye className="h-3.5 w-3.5" /> : <Code className="h-3.5 w-3.5" />}
        </IconButton>
        <IconButton label={copied ? t('ui.widget.copied') : t('ui.widget.copyCode')} size="xs" onClick={copyCode}>
          {copied ? <Check className="h-3.5 w-3.5 text-ok" /> : <Copy className="h-3.5 w-3.5" />}
        </IconButton>
        {payload.kind === 'svg' && svgText ? (
          <>
            <IconButton label={t('ui.widget.copyImage')} size="xs" onClick={() => void exportPng('clipboard')}>
              <Image className="h-3.5 w-3.5" />
            </IconButton>
            <IconButton label={t('ui.widget.saveImage')} size="xs" onClick={() => void exportPng('file')}>
              <ImageDown className="h-3.5 w-3.5" />
            </IconButton>
          </>
        ) : null}
        <IconButton label={t('ui.widget.download')} size="xs" onClick={downloadCode}>
          <Download className="h-3.5 w-3.5" />
        </IconButton>
        <IconButton label={zoom ? t('ui.widget.zoomOut') : t('ui.widget.zoomIn')} size="xs" onClick={() => setZoom((v) => !v)}>
          {zoom ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
        </IconButton>
      </div>
    </figcaption>
  )

  const body = payload.kind === 'svg' ? (
    image ? <div className="widget-host grid place-items-center overflow-auto bg-canvas p-4"><SvgImage markup={image.markup} alt={payload.title} /></div> : svgText ? (
      <div className="widget-host grid place-items-center overflow-auto bg-canvas p-4 [&_svg]:h-auto [&_svg]:max-w-full" dangerouslySetInnerHTML={{ __html: svgText }} />
    ) : (
      <div>
        <p role="note" className="border-b border-line bg-warn/8 px-4 py-2 text-2xs text-warn">{t('ui.widget.renderFailed')}</p>
        <pre tabIndex={0} className="max-h-dialog overflow-auto whitespace-pre-wrap break-words px-4 py-3 font-mono text-compact leading-code text-muted">{payload.code}</pre>
      </div>
    )
  ) : (
    <HtmlSandboxFrame code={previewCode ?? payload.code} offline={isChartKind(payload.kind)} title={payload.title} active={active && !showCode && !settingsOpen} viewKey={zoom ? 'expanded' : 'inline'} onEscape={zoom ? () => setZoom(false) : undefined} onFocusBoundary={zoom ? backward => { const buttons = Array.from(figureRef.current?.querySelectorAll<HTMLButtonElement>('figcaption button:not([disabled])') ?? []); (backward ? buttons.at(-1) : buttons[0])?.focus() } : undefined} />
  )

  return (
    <figure
      ref={figureRef}
      role={zoom ? 'dialog' : undefined}
      aria-modal={zoom ? true : undefined}
      aria-label={zoom ? payload.title : undefined}
      tabIndex={zoom ? -1 : undefined}
      className={cn(
        'group/widget overflow-hidden rounded-xl border border-line bg-surface',
        zoom && 'fixed inset-4 z-expanded my-0 shadow-pop',
      )}
    >
      {header}
      <div className={cn(zoom && 'h-zoom overflow-auto')}>
        <div hidden={showCode}>{body}</div>
        <pre hidden={!showCode} tabIndex={0} className="max-h-dialog overflow-auto whitespace-pre-wrap break-words bg-surface/60 px-4 py-3 font-mono text-compact leading-code text-muted">{payload.code}</pre>
      </div>
      {zoom ? (
        <button type="button" aria-label={t('ui.widget.zoomOut')} className="absolute inset-0 -z-10" onClick={() => setZoom(false)} />
      ) : null}
    </figure>
  )
})

/** 格式边界：分隔文本始终走表格，只有 HTML 与图表适配器进入沙箱。 */
export const WidgetCard = memo(function WidgetCard(props: {payload: WidgetPayload; previewCode?: string; active?: boolean}) {
  return isTableKind(props.payload.kind)
    ? <DataTable source={props.payload.code} kind={props.payload.kind} title={props.payload.title} />
    : isChartKind(props.payload.kind) && props.previewCode === undefined
      ? <SpecChart source={props.payload.code} kind={props.payload.kind} payload={props.payload} active={props.active} />
      : <WidgetContent {...props} />
})

/** 工具运行中的占位：骨架 + 轮播加载提示，与问题卡片的挂起态保持一致。 */
export const WidgetLoading = memo(function WidgetLoading({ preview }: { preview: WidgetPreview | null }) {
  useLocale()
  const visible = usePageVisible()
  const messages = preview?.loadingMessages.length ? preview.loadingMessages : [t('ui.widget.loading')]
  const [index, setIndex] = useState(0)
  useEffect(() => {
    if (!visible || messages.length <= 1) return
    const timer = setInterval(() => setIndex((v) => (v + 1) % messages.length), 1400)
    return () => clearInterval(timer)
  }, [visible, messages.length])
  return (
    <div className="flex items-center gap-2 rounded-xl border border-line bg-raised/40 px-3 py-2.5" role="status" aria-label={t('ui.widget.loading')}>
      <Spinner />
      <span className="min-w-0 truncate text-compact text-muted">{preview?.title || t('ui.widget.loading')}</span>
      <span className="ml-auto shrink-0 text-2xs text-faint">{messages[index % messages.length]}</span>
    </div>
  )
})
