import type { ResourceText } from '@/types'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { useEffect, useRef, useState } from 'react'
import { t, useLocale } from '@/i18n'
import { runtimeRequest } from '@/lib/http'
import type { PluginPackage, PluginPackageCatalog } from '@/lib/protocol.generated'
import { Button } from '@/components/ui/primitives'
import { JsonSource } from '@/components/ui/JsonSource'

export function PluginHookReview({ item, busy, onTrust }: { item: PluginPackage; busy: boolean; onTrust: () => void }) {
  useLocale()
  if (!item.hooks?.length) return null
  return <details className="rounded-lg border border-line p-3">
    <summary className="cursor-pointer text-xs text-ink focus-visible:outline-brand">{t(item.hooks_trusted ? 'ui.extensions.hooksReviewTrusted' : 'ui.extensions.hooksReviewUntrusted', { count: item.hooks.length })}</summary>
    <div className="mt-3 space-y-3">
      <p className="text-xs leading-relaxed text-muted">{t('ui.extensions.hooksTrustHint')}</p>
      {item.hooks.map((hook, index) => <div key={index} className="space-y-1 rounded-lg bg-raised p-2">
        <p className="text-xs text-ink">{t('ui.extensions.hookMetadata', {event: hook.event, type: hook.type, seconds: hook.timeout})}</p>
        {hook.type === 'mcp_tool' ? <><code className="block break-all text-xs text-muted">{hook.server}.{hook.tool}</code><JsonSource text={JSON.stringify(hook.input, null, 2) ?? 'null'} /></>
          : <pre className="whitespace-pre-wrap break-all font-mono text-xs text-muted">{hook.command}</pre>}
      </div>)}
      <Button size="sm" disabled={busy} onClick={onTrust}>{t(item.hooks_trusted ? 'ui.extensions.hooksRevoke' : 'ui.extensions.hooksTrust')}</Button>
    </div>
  </details>
}

export function PluginHooksSection() {
  useLocale()
  const [items, setItems] = useState<PluginPackage[]>([])
  const [error, setError] = useState<ResourceText>('')
  const [busy, setBusy] = useState(false)
  const mounted = useRef(false)
  const locked = useRef(false)
  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    void runtimeRequest<PluginPackageCatalog>('/plugin-packages', 'GET', undefined, {signal: controller.signal}).then(data => { if (!controller.signal.aborted) setItems(data.packages) }).catch(failure => { if (!controller.signal.aborted) setError(failureText(failure)) })
    return () => { mounted.current = false; controller.abort() }
  }, [])
  async function trust(item: PluginPackage) {
    if (locked.current) return
    locked.current = true; setBusy(true); setError('')
    try {
      const data = await runtimeRequest<PluginPackageCatalog>(`/plugin-packages/${encodeURIComponent(item.name)}/hooks/trust`, 'POST', { fingerprint: item.hooks_fingerprint, trusted: !item.hooks_trusted })
      if (mounted.current) setItems(data.packages)
    } catch (failure) {
      // 写请求不重放；读回实际状态，失败时保留可重试入口。
      try {
        const data = await runtimeRequest<PluginPackageCatalog>('/plugin-packages')
        if (mounted.current) setItems(data.packages)
      } catch { /* 原始错误保留给用户。 */ }
      if (mounted.current) setError(failureText(failure))
    } finally { locked.current = false; if (mounted.current) setBusy(false) }
  }
  return <section className="space-y-3" aria-label={t('ui.extensions.pluginHooks')}>
    <h4 className="text-sm font-medium text-ink">{t('ui.extensions.pluginHooks')}</h4>
    {error && <p role="alert" className="text-xs text-danger">{noticeText(error)}</p>}
    {items.filter(item => item.hooks?.length).map(item => <div key={item.name} className="space-y-2"><p className="text-xs text-muted">{item.display_name}</p><PluginHookReview item={item} busy={busy} onTrust={() => void trust(item)} /></div>)}
  </section>
}
