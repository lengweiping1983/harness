import { t, useLocale } from '@/i18n'
import { useEffect, useRef, useState, type ComponentType, type ReactNode } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'

type PanelLoader<P extends object> = () => Promise<{ default: ComponentType<P> }>

/** 首次显示才加载模块；取消后忽略迟到结果，失败通过重载清除模块错误缓存。 */
export function DeferredPanel<P extends object>({ load, panelProps, label, fallbackClassName, renderFallback, onReady, skeleton = false }: {
  load: PanelLoader<P>; panelProps: P; label: string; fallbackClassName?: string; renderFallback?: (content: ReactNode) => ReactNode; onReady?: () => void; skeleton?: boolean
}) {
  useLocale()
  const [loaded, setLoaded] = useState<{ load: PanelLoader<P>; panel: ComponentType<P> } | null>(null)
  const [failed, setFailed] = useState(false)
  const readyRef = useRef(onReady)
  readyRef.current = onReady
  useEffect(() => {
    let live = true
    setFailed(false)
    const timer = setTimeout(() => { if (live) setFailed(true) }, 10000)
    void Promise.resolve().then(load).then(module => {
      if (live) {readyRef.current?.(); setLoaded({ load, panel: module.default })}
    }).catch(() => { if (live) setFailed(true) }).finally(() => clearTimeout(timer))
    return () => { live = false; clearTimeout(timer) }
  }, [load])
  if (loaded?.load === load) {
    const Panel = loaded.panel
    return <Panel {...panelProps} />
  }
  const fallback = <section aria-busy={!failed} aria-label={label} className={fallbackClassName ?? 'space-y-2 px-1 py-3 text-xs text-muted'}>
    {failed ? <><p role="alert">{t('ui.deferred.panel.failed', { name: label })}</p>
      <button type="button" onClick={() => window.location.reload()} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-brand hover:bg-raised focus-visible:outline-brand">
        <RefreshCw className="h-3.5 w-3.5" />{t('ui.deferred.reload')}
      </button></> : <p role="status" className="flex items-center gap-2"><Loader2 className="h-3.5 w-3.5 animate-spin" />{t('ui.deferred.panel.loading', { name: label })}</p>}
  </section>
  const content = skeleton && !failed ? <>{fallback}<div aria-hidden="true" className="space-y-3 rounded-xl border border-line p-4 motion-safe:animate-pulse"><div className="h-4 w-1/3 rounded bg-raised" /><div className="h-3 w-2/3 rounded bg-raised" /><div className="h-20 rounded bg-raised" /><div className="h-8 w-1/2 rounded bg-raised" /></div></> : fallback
  return renderFallback ? renderFallback(content) : content
}
