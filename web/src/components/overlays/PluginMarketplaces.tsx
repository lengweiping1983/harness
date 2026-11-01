import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { failureText } from '@/lib/resourceError'
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { t, useLocale } from '@/i18n'
import { ApiError, isUnconfirmedWrite, runtimeRequest } from '@/lib/http'
import type { MarketplaceCatalog } from '@/lib/protocol.generated'
import { Button, RefreshButton } from '@/components/ui/primitives'
import { DirectoryField } from '@/components/ui/DirectoryField'

export type MarketplaceRefresh = {refresh: () => Promise<void>; isBusy: () => boolean}

export const PluginMarketplaces = forwardRef<MarketplaceRefresh, {onPreview: (request: {path: string; name: string; fingerprint: string}) => void; disabled?: boolean; managedRefresh?: boolean; onWorking?: (working: boolean) => void}>(function PluginMarketplaces({ onPreview, disabled = false, managedRefresh = false, onWorking }, ref) {
  useLocale()
  const [data, setData] = useState<MarketplaceCatalog | null>(null)
  const [path, setPath] = useState('')
  const [error, setError] = useState<ResourceText>('')
  const [working, setBusy] = useState(false)
  const busy = working || disabled
  const active = useRef(false), locked = useRef(false)
  const initialRead = useRef<AbortController>()
  const load = () => runtimeRequest<MarketplaceCatalog>('/plugin-marketplaces')
  const reload = async () => {
    const controller = initialRead.current = new AbortController()
    const result = await runtimeRequest<MarketplaceCatalog>('/plugin-marketplaces', 'GET', undefined, {signal: controller.signal})
    if (!controller.signal.aborted && active.current) setData(result)
  }
  useEffect(() => {
    active.current = true
    const controller = initialRead.current = new AbortController()
    void runtimeRequest<MarketplaceCatalog>('/plugin-marketplaces', 'GET', undefined, { signal: controller.signal }).then(result => { if (!controller.signal.aborted) setData(result) }).catch(failure => { if (!controller.signal.aborted) setError(failureText(failure)) })
    return () => { active.current = false; controller.abort(); initialRead.current?.abort() }
  }, [])
  const run = async (action: () => Promise<void>, reading = false, fromPage = false) => {
    if (locked.current || disabled && !fromPage) return
    initialRead.current?.abort()
    locked.current = true; setBusy(true); onWorking?.(true); setError('')
    try { await action() } catch (failure) {
      if (active.current) {
        if (!reading && failure instanceof ApiError && isUnconfirmedWrite(failure.failure)) {
          try { const result = await load(); if (active.current) setData(result) } catch { /* 原始错误保留；不重放注册操作。 */ }
          if (active.current) setError({template: {key: 'ui.extensions.unknownResult', params: {reason: failureText(failure)}}})
        } else setError(failureText(failure))
      }
    } finally { locked.current = false; if (active.current) {setBusy(false); onWorking?.(false)} }
  }
  useImperativeHandle(ref, () => ({isBusy: () => locked.current, refresh: () => run(reload, true, true)}))
  return <section aria-label={t('ui.extensions.marketplaces')} className="refresh-action-container space-y-3 rounded-xl border border-line p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-medium text-ink">{t('ui.extensions.marketplaces')}</h4>{!managedRefresh && <RefreshButton size="sm" variant="ghost" label={t('ui.extensions.reloadMarkets')} disabled={busy} onClick={() => void run(reload, true)} />}</div>
    <p className="text-xs leading-relaxed text-muted">{t('ui.extensions.marketHint')}</p>
    {error && <p role="alert" className="break-words text-xs text-danger">{noticeText(error)}</p>}
    <DirectoryField label={t('ui.extensions.marketRoot')} value={path} onChange={setPath} disabled={busy} />
    <Button size="sm" disabled={busy || !path.trim()} loading={busy} onClick={() => void run(async () => {
      const result = await runtimeRequest<MarketplaceCatalog>('/plugin-marketplaces', 'POST', { path: path.trim() }, { timeout: 120_000 })
      if (active.current) { setData(result); setPath('') }
    })}>{t('ui.extensions.addMarket')}</Button>
    {data?.marketplaces.map(market => <div key={market.path} className="space-y-2 border-t border-line pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm font-medium text-ink">{market.display_name || market.name || t('ui.extensions.invalidMarket')}</span>{market.registered && <Button size="sm" variant="ghost" disabled={busy} onClick={() => void run(async () => {
        const result = await runtimeRequest<MarketplaceCatalog>('/plugin-marketplaces', 'DELETE', { path: market.path })
        if (active.current) setData(result)
      })}>{t('ui.extensions.removeMarket')}</Button>}</div>
      <p className="break-all font-mono text-2xs text-muted">{market.path}</p>
      {market.error && <p role="alert" className="break-words text-xs text-danger">{market.error}</p>}
      {market.plugins.map(plugin => <div key={plugin.name} className="flex min-w-0 flex-wrap items-center gap-2 rounded-lg bg-raised/30 p-2">
        <div className="min-w-0 flex-1"><p className="break-words text-xs font-medium text-ink">{plugin.name}</p><p className="break-words text-2xs text-muted">{plugin.category} · {plugin.source_type}</p>{plugin.error && <p className="break-words text-2xs text-warn">{plugin.error}</p>}{plugin.installation === 'NOT_AVAILABLE' && <p className="text-2xs text-warn">{t('ui.extensions.marketUnavailable')}</p>}</div>
        <Button size="sm" disabled={busy || !!plugin.error || plugin.installation === 'NOT_AVAILABLE'} onClick={() => {
          setError('')
          onPreview({path: market.path, name: plugin.name, fingerprint: market.fingerprint})
        }}>{t('ui.extensions.preview')}</Button>
      </div>)}
    </div>)}
    {data && !data.marketplaces.length && <p className="text-xs text-muted">{t('ui.extensions.noMarkets')}</p>}
  </section>
})
