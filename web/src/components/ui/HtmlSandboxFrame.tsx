import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { usePageVisible } from '@/hooks/usePageVisible'
import { t, useLocale } from '@/i18n'
import { RotateCcw } from 'lucide-react'
import { IconButton } from './primitives'

const CSP = "default-src 'none'; script-src 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com https://esm.sh; style-src 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com; img-src data: https:; font-src https: data:; connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'"
const CHANNEL = 'harness-widget'
const MIN_HEIGHT = 40
const MAX_HEIGHT = 2000
const VARIABLES = ['--c-ink', '--c-muted', '--c-faint', '--c-line', '--c-line-strong', '--c-surface', '--c-canvas', '--c-raised', '--c-brand', '--font-sans', '--font-mono']

function themeSnapshot() {
  const root = document.documentElement
  const style = getComputedStyle(root)
  return { dark: root.classList.contains('dark'), css: VARIABLES.map(name => `${name}:${style.getPropertyValue(name)}`).join(';') }
}

/** 完整文档与片段走同一沙箱，CSP 和消息引导在模型脚本前安装。 */
export function htmlDocument(code: string, identity: string): string {
  const doc = new DOMParser().parseFromString(code, 'text/html')
  doc.querySelectorAll('base,meta[http-equiv]').forEach(node => node.remove())
  const theme = themeSnapshot()
  doc.documentElement.classList.toggle('dark', theme.dark)
  const policy = doc.createElement('meta')
  policy.httpEquiv = 'Content-Security-Policy'
  policy.content = CSP
  const charset = doc.createElement('meta')
  charset.setAttribute('charset', 'utf-8')
  const viewport = doc.createElement('meta')
  viewport.name = 'viewport'
  viewport.content = 'width=device-width, initial-scale=1'
  const style = doc.createElement('style')
  style.textContent = `:root{${theme.css};color-scheme:${theme.dark ? 'dark' : 'light'};--color-text-primary:rgb(var(--c-ink));--color-text-secondary:rgb(var(--c-muted));--color-text-hint:rgb(var(--c-faint));--color-border-tertiary:rgb(var(--c-line));--color-background-primary:rgb(var(--c-surface));--color-background-secondary:rgb(var(--c-canvas));}*,*::before,*::after{box-sizing:border-box}html{background:transparent}body{margin:0;min-height:${MIN_HEIGHT}px;background:transparent;color:var(--color-text-primary);font-family:var(--font-sans);line-height:1.6}svg,canvas,img{max-width:100%}`
  const bootstrap = doc.createElement('script')
  bootstrap.textContent = `(()=>{
    const id=${JSON.stringify(identity)},channel=${JSON.stringify(CHANNEL)};
    const post=parent.postMessage.bind(parent),raf=requestAnimationFrame.bind(window),Observer=ResizeObserver;
    const send=(kind,data={})=>post({channel,id,kind,...data},'*');
    let lastFailure=null,queued=false,pendingPaint=null,trapFocus=false;
    const notify=()=>{
      if(!document.body)return;
      send('resize',{height:Math.max(document.body.scrollHeight,document.body.getBoundingClientRect().height)});
    };
    const paintReady=ticket=>raf(()=>raf(()=>{if(document.readyState!=='loading')send('ready',{paint:ticket})}));
    const resize=()=>{if(queued)return;queued=true;raf(()=>{queued=false;notify()})};
    const fail=message=>{lastFailure=message;send('error',{message})};
    addEventListener('error',e=>fail(e.message||e.target?.src||''),true);
    addEventListener('unhandledrejection',e=>fail(String(e.reason?.message||e.reason||'')));
    addEventListener('message',e=>{
      const data=e.data;
      if(e.source!==parent||data?.channel!==channel||data?.id!==id||data?.kind!=='theme'||typeof data.css!=='string')return;
      document.documentElement.classList.toggle('dark',!!data.dark);
      for(const value of data.css.split(';')){const at=value.indexOf(':');if(at>0)document.documentElement.style.setProperty(value.slice(0,at),value.slice(at+1))}
      document.documentElement.style.colorScheme=data.dark?'dark':'light';
      notify();
      trapFocus=!!data.trapFocus;
      pendingPaint=typeof data.paint==='string'?data.paint:null;
      if(pendingPaint&&document.readyState!=='loading')paintReady(pendingPaint);
      if(lastFailure!==null)send('error',{message:lastFailure});
    });
    addEventListener('DOMContentLoaded',()=>{new Observer(resize).observe(document.body);notify();if(pendingPaint)paintReady(pendingPaint)});
    addEventListener('load',notify);
    addEventListener('keydown',event=>{
      if(!event.isTrusted||event.defaultPrevented||event.isComposing)return;
      if(event.key==='Escape')send('escape');
      else if(event.key==='Tab'&&trapFocus){
        const nodes=[...document.querySelectorAll('button,input,select,textarea,a[href],[tabindex]')].filter(node=>!node.disabled&&node.tabIndex>=0&&node.getClientRects().length);
        const boundary=event.shiftKey?nodes[0]:nodes.at(-1);
        if(!nodes.length||document.activeElement===boundary){event.preventDefault();send('focus-boundary',{backward:event.shiftKey})}
      }
    });
    document.fonts?.ready.then(resize).catch(()=>{});
  })();`

  doc.head.prepend(charset, policy, viewport, style, bootstrap)
  return '<!doctype html>\n' + doc.documentElement.outerHTML
}

/** 脚本在 opaque origin 中运行；消息绑定所属窗口、代码代次及实例。主题更新不重放脚本。 */
export const HtmlSandboxFrame = memo(function HtmlSandboxFrame({ code, title, className, active = true, viewKey = 'inline', onEscape, onFocusBoundary }: {
  code: string
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
  const identity = useMemo(() => crypto.randomUUID(), [code, revision])
  const paintTicket = useMemo(() => crypto.randomUUID(), [identity, renderActive, viewKey])
  const srcDoc = useMemo(() => htmlDocument(code, identity), [code, identity])
  const [readyTicket, setReadyTicket] = useState<string | null>(null)
  const [size, setSize] = useState({ id: identity, height: MIN_HEIGHT })
  const [failure, setFailure] = useState<{id: string; message: string} | null>(null)
  const publishTheme = () => frame.current?.contentWindow?.postMessage({channel: CHANNEL, id: identity, kind: 'theme', paint: renderActive ? paintTicket : null, trapFocus: Boolean(onFocusBoundary), ...themeSnapshot()}, '*')
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow) return
      const data = event.data
      if (!data || data.channel !== CHANNEL || data.id !== identity) return
      if (data.kind === 'resize' && typeof data.height === 'number' && Number.isFinite(data.height)) {
        setSize({id: identity, height: Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.ceil(data.height)))})
      } else if (data.kind === 'error' && typeof data.message === 'string') {
        setFailure({id: identity, message: data.message})
      } else if (data.kind === 'ready' && renderActive && data.paint === paintTicket) {
        setReadyTicket(paintTicket)
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
  return <div className="relative min-w-0" aria-busy={!ready}>
    {!ready ? <p role="status" className="absolute inset-0 z-10 m-0 grid place-items-center bg-surface/90 px-3 py-2 text-xs text-muted">{t('ui.widget.initializing')}</p> : null}
    {failure?.id === identity ? <div role="alert" className="flex items-start gap-2 border-b border-line bg-warn/10 px-3 py-2 text-xs text-warn"><div className="min-w-0 flex-1"><p>{t('ui.widget.runtimeError')}</p><pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words">{failure.message || t('ui.widget.resourceFailed')}</pre></div><IconButton label={t('ui.widget.restart')} size="xs" onClick={() => setRevision(value => value + 1)}><RotateCcw className="h-3.5 w-3.5" /></IconButton></div> : null}
    <iframe key={identity} ref={frame} title={title} sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={srcDoc} onLoad={publishTheme}
      tabIndex={ready ? 0 : -1} style={{height: size.id === identity ? size.height : MIN_HEIGHT}} className={className ?? 'block w-full border-0 bg-transparent'} />
  </div>
})
