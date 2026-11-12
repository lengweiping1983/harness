import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { sandboxBootstrap } from '@/lib/htmlSandboxRuntime'
import { usePageVisible } from '@/hooks/usePageVisible'
import { t, useLocale } from '@/i18n'
import { RotateCcw } from 'lucide-react'
import { IconButton } from './primitives'

const CSP = "default-src 'none'; script-src 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com https://esm.sh; style-src 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com; img-src data: https:; font-src https: data:; connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'"
const CHANNEL = 'harness-widget'
const MIN_HEIGHT = 40
const MAX_HEIGHT = 2000
const INITIALIZATION_TIMEOUT = 10000
const VARIABLES = ['--c-ink', '--c-muted', '--c-faint', '--c-line', '--c-line-strong', '--c-surface', '--c-canvas', '--c-raised', '--c-brand', '--font-sans', '--font-mono']

function themeSnapshot() {
  const root = document.documentElement
  const style = getComputedStyle(root)
  return { dark: root.classList.contains('dark'), css: VARIABLES.map(name => `${name}:${style.getPropertyValue(name)}`).join(';') }
}

/** 完整文档与片段走同一沙箱，CSP 和消息引导在模型脚本前安装。 */
export function htmlDocument(code: string, identity: string, offline = false): string {
  const doc = new DOMParser().parseFromString(code, 'text/html')
  doc.querySelectorAll('base,meta[http-equiv]').forEach(node => node.remove())
  const theme = themeSnapshot()
  doc.documentElement.classList.toggle('dark', theme.dark)
  const policy = doc.createElement('meta')
  policy.httpEquiv = 'Content-Security-Policy'
  policy.content = offline ? "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'" : CSP
  const charset = doc.createElement('meta')
  charset.setAttribute('charset', 'utf-8')
  const viewport = doc.createElement('meta')
  viewport.name = 'viewport'
  viewport.content = 'width=device-width, initial-scale=1'
  const style = doc.createElement('style')
  style.textContent = `:root{${theme.css};color-scheme:${theme.dark ? 'dark' : 'light'};--color-text-primary:rgb(var(--c-ink));--color-text-secondary:rgb(var(--c-muted));--color-text-hint:rgb(var(--c-faint));--color-border-tertiary:rgb(var(--c-line));--color-background-primary:rgb(var(--c-surface));--color-background-secondary:rgb(var(--c-canvas));}*,*::before,*::after{box-sizing:border-box}html{background:transparent}body{margin:0;min-height:${MIN_HEIGHT}px;background:transparent;color:var(--color-text-primary);font-family:var(--font-sans);line-height:1.6}svg,canvas,img{max-width:100%}`
  const bootstrap = doc.createElement('script')
  bootstrap.textContent = sandboxBootstrap(identity)

  doc.head.prepend(charset, policy, viewport, style, bootstrap)
  return '<!doctype html>\n' + doc.documentElement.outerHTML
}

/** 脚本在 opaque origin 中运行；消息绑定所属窗口、代码代次及实例。主题更新不重放脚本。 */
export const HtmlSandboxFrame = memo(function HtmlSandboxFrame({ code, title, className, active = true, viewKey = 'inline', onEscape, onFocusBoundary, offline = false }: {
  code: string
  offline?: boolean
  title: string
  className?: string
  active?: boolean
  viewKey?: string
  onEscape?: () => void
  onFocusBoundary?: (backward: boolean) => void
}) {
  useLocale()
  const pageVisible = usePageVisible()
  const renderActive = active && pageVisible
  const frame = useRef<HTMLIFrameElement>(null)
  const [revision, setRevision] = useState(0)
  const identity = useMemo(() => crypto.randomUUID(), [code, revision, offline])
  const paintTicket = useMemo(() => crypto.randomUUID(), [identity, renderActive, viewKey])
  const srcDoc = useMemo(() => htmlDocument(code, identity, offline), [code, identity])
  const [readyTicket, setReadyTicket] = useState<string | null>(null)
  const [size, setSize] = useState({ id: identity, height: MIN_HEIGHT })
  const [failure, setFailure] = useState<{id: string; message: string; copy?: string; timeout?: boolean} | null>(null)
  const publishTheme = () => frame.current?.contentWindow?.postMessage({channel: CHANNEL, id: identity, kind: 'theme', active: renderActive, paint: renderActive ? paintTicket : null, trapFocus: Boolean(onFocusBoundary), ...themeSnapshot()}, '*')
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow) return
      const data = event.data
      if (!data || data.channel !== CHANNEL || data.id !== identity) return
      if (data.kind === 'boot') { publishTheme(); return }
      if (data.kind === 'resize' && renderActive && typeof data.height === 'number' && Number.isFinite(data.height)) {
        const height = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.ceil(data.height)))
        setSize(previous => previous.id === identity && previous.height === height ? previous : {id: identity, height})
      } else if (data.kind === 'error' && typeof data.message === 'string') {
        setFailure({id: identity, message: data.message, copy: data.copy === 'ui.chart.inlineData' ? data.copy : undefined})
      } else if (data.kind === 'ready' && renderActive && data.paint === paintTicket) {
        setReadyTicket(paintTicket)
        setFailure(previous => previous?.id === identity && previous.timeout ? null : previous)
      } else if (data.kind === 'escape' && renderActive) onEscape?.()
      else if (data.kind === 'focus-boundary' && renderActive && typeof data.backward === 'boolean') onFocusBoundary?.(data.backward)
    }
    const observer = new MutationObserver(publishTheme)
    if (renderActive) observer.observe(document.documentElement, {attributes: true, attributeFilter: ['class', 'style', 'data-theme']})
    window.addEventListener('message', onMessage)
    publishTheme()
    return () => { observer.disconnect(); window.removeEventListener('message', onMessage) }
  }, [identity, paintTicket, renderActive, onEscape, onFocusBoundary])
  const ready = renderActive && readyTicket === paintTicket
  const currentFailure = failure?.id === identity ? failure : null
  useEffect(() => {
    if (!renderActive || ready) return
    const timer = setTimeout(() => setFailure(previous => previous?.id === identity ? previous : {id: identity, message: '', timeout: true}), INITIALIZATION_TIMEOUT)
    return () => clearTimeout(timer)
  }, [identity, paintTicket, renderActive, ready])
  return <div className="relative min-w-0" aria-busy={renderActive && !ready && !currentFailure}>
    {!ready && !currentFailure ? <p role="status" className="absolute inset-0 z-10 m-0 grid place-items-center bg-surface/90 px-3 py-2 text-xs text-muted">{t('ui.widget.initializing')}</p> : null}
    {failure?.id === identity ? <div role="alert" className="flex items-start gap-2 border-b border-line bg-warn/10 px-3 py-2 text-xs text-warn"><div className="min-w-0 flex-1"><p>{t(currentFailure?.timeout ? 'ui.widget.initializationFailed' : 'ui.widget.runtimeError')}</p>{!currentFailure?.timeout ? <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words">{currentFailure?.copy ? t(currentFailure.copy) : failure?.message || t('ui.widget.resourceFailed')}</pre> : null}</div><IconButton label={t('ui.widget.restart')} size="xs" onClick={() => setRevision(value => value + 1)}><RotateCcw className="h-3.5 w-3.5" /></IconButton></div> : null}
    <iframe key={identity} ref={frame} title={title} sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={srcDoc} onLoad={publishTheme}
      tabIndex={ready ? 0 : -1} style={{height: size.id === identity ? size.height : MIN_HEIGHT}} className={className ?? 'block w-full border-0 bg-transparent'} />
  </div>
})
