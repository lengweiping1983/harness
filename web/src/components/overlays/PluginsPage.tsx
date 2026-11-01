import type { ResourceText } from '@/types'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { PluginHookReview } from './PluginHooks'
import { AgentCreator } from './AgentCreator'
import { PluginSourceEditor } from './PluginSourceEditor'
import { PluginMarketplaces, type MarketplaceRefresh } from './PluginMarketplaces'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, Download, Package, Power, Search, Trash2 } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { ApiError, isUnconfirmedWrite, runtimeRequest } from '@/lib/http'
import type { PluginAppReference, PluginDraft, PluginPackage, PluginPackageCatalog } from '@/lib/protocol.generated'
import { Badge, Button, EmptyHint, RefreshButton, Spinner } from '@/components/ui/primitives'
import { DirectoryField } from '@/components/ui/DirectoryField'
import { useAppStore } from '@/store/app'
import { usePluginSourceDrafts } from '@/store/pluginSourceDrafts'
import { usePluginReview } from '@/store/pluginReview'

type Activity = {kind: 'preview' | 'read' | 'write'; origin?: string; controller?: AbortController}
type OperationContext = {signal?: AbortSignal; current: () => boolean}

export function PluginsPage() {
  const sessionId = useAppStore(state => state.activeSessionId)
  return <PluginsPageContent key={sessionId ?? 'shared'} sessionId={sessionId} />
}

function PluginsPageContent({sessionId}: {sessionId: string | null}) {
  const locale = useLocale()
  const review = usePluginReview(state => state.request)
  const [data, setData] = useState<PluginPackageCatalog | null>(null)
  const [path, setPath] = useState('')
  const [reviewed, setReviewed] = useState<PluginDraft | null>(null)
  const preview = reviewed?.package ?? null
  const [query, setQuery] = useState('')
  const [error, setError] = useState<ResourceText>('')
  const [notice, setNotice] = useState<ResourceText>('')
  const [busy, setBusy] = useState('')
  const [marketWorking, setMarketWorking] = useState(false)
  const marketplaces = useRef<MarketplaceRefresh>(null)
  const [removing, setRemoving] = useState<string | null>(null)
  const editorRoot = usePluginSourceDrafts(state => state.root)
  const mounted = useRef(false)
  const activity = useRef<Activity | null>(null)
  const sequence = useRef(0)
  const loadAbort = useRef<AbortController | null>(null)
  const load = useCallback(async () => {
    const request = ++sequence.current
    loadAbort.current?.abort()
    const controller = new AbortController()
    loadAbort.current = controller
    try {
      const catalog = await runtimeRequest<PluginPackageCatalog>('/plugin-packages', 'GET', undefined, {signal: controller.signal})
      if (mounted.current && request === sequence.current) setData(catalog)
      return catalog
    } catch (failure) {
      if (mounted.current && request === sequence.current) setError(failureText(failure))
      throw failure
    } finally {
      if (loadAbort.current === controller) loadAbort.current = null
    }
  }, [])
  useEffect(() => {
    mounted.current = true
    void load().catch(() => undefined)
    return () => {
      mounted.current = false; sequence.current += 1
      loadAbort.current?.abort(); loadAbort.current = null
      activity.current?.controller?.abort()
      activity.current = null
    }
  }, [load])
  const perform = useCallback(async (key: string, operation: (context: OperationContext) => Promise<unknown>,
    message?: ResourceText, kind: Activity['kind'] = 'write', origin?: string) => {
    const previous = activity.current
    if (previous && (kind !== 'preview' || previous.kind !== 'preview')) return
    ++sequence.current; loadAbort.current?.abort()
    previous?.controller?.abort()
    if (previous?.origin && previous.origin !== origin) usePluginReview.getState().consume(previous.origin)
    const token: Activity = {kind, origin, controller: kind === 'preview' ? new AbortController() : undefined}
    activity.current = token
    const current = () => mounted.current && activity.current === token
    setBusy(key); setError(''); setNotice('')
    try {
      await operation({signal: token.controller?.signal, current})
      if (!current()) return
      if (kind !== 'read') await load()
      if (current() && message) setNotice(message)
    } catch (failure) {
      if (!current()) return
      if (kind === 'write' && failure instanceof ApiError && isUnconfirmedWrite(failure.failure)) {
        try { await load() } catch { /* 读回失败仍保留原始未知写入错误。 */ }
        if (current()) setError({template: {key: 'ui.extensions.unknownResult', params: {reason: failureText(failure)}}})
      } else setError(failureText(failure))
    } finally {
      if (activity.current === token) {
        activity.current = null
        if (mounted.current) {
          setBusy('')
          if (origin) usePluginReview.getState().consume(origin)
        }
      }
    }
  }, [load])
  const previewPlugin = useCallback((source: string, read: (signal?: AbortSignal) => Promise<PluginDraft>, origin?: string) =>
    perform('preview', async ({signal, current}) => {
      setPath(source); setReviewed(null)
      const draft = await read(signal)
      if (current()) {setPath(draft.path); setReviewed(draft)}
    }, undefined, 'preview', origin), [perform])
  const previewSource = useCallback((source: string, origin?: string) => previewPlugin(source, async signal => ({
    path: source, package: await runtimeRequest<PluginPackage>('/plugin-packages/preview', 'POST', {path: source}, {timeout: 120_000, signal}),
  }), origin), [previewPlugin])
  const discardPreview = useCallback(() => {
    const token = activity.current
    if (token?.kind === 'preview') {
      activity.current = null; token.controller?.abort(); setBusy('')
    }
    const request = usePluginReview.getState().request
    if (request?.sessionId === sessionId) usePluginReview.getState().consume(request.id)
    setReviewed(null)
  }, [sessionId])
  useEffect(() => {
    if (!review || review.sessionId !== sessionId) return
    const pending = activity.current
    if (pending && (pending.kind !== 'preview' || pending.origin === review.id)) return
    void previewSource(review.path, review.id)
  }, [review, sessionId, previewSource, busy])
  const visible = (data?.packages ?? []).filter(item => `${item.display_name} ${item.name} ${item.description}`.toLocaleLowerCase(locale).includes(query.trim().toLocaleLowerCase(locale)))
  return <div className="mx-auto max-w-4xl space-y-5">
    <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 flex-1"><h3 className="ui-section-title">{t('ui.extensions.plugins')}</h3><p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">{t('ui.extensions.pluginsHint')}</p></div>
      <RefreshButton size="sm" variant="ghost" label={t('ui.extensions.refreshPlugins')} disabled={!!busy || marketWorking} loading={busy === 'refresh'} onClick={() => {
        if (marketplaces.current?.isBusy()) return
        void perform('refresh', async () => {
          const results = await Promise.allSettled([load(), marketplaces.current?.refresh()])
          for (const result of results) if (result.status === 'rejected') throw result.reason
        }, undefined, 'read')
      }} />
    </header>
    {error && <p role="alert" className="break-words rounded-lg border border-danger/25 bg-danger/5 p-3 text-xs text-danger">{noticeText(error)}</p>}
    {notice && <p role="status" className="flex items-center gap-2 rounded-lg bg-brand/[0.12] p-3 text-xs text-brand"><Check className="h-3.5 w-3.5" />{noticeText(notice)}</p>}
    <AgentCreator kind="plugin" sessionId={sessionId} disabled={!!busy} />
    <PluginMarketplaces ref={marketplaces} managedRefresh onWorking={setMarketWorking} disabled={!!busy} onPreview={request => void previewPlugin('', signal =>
      runtimeRequest<PluginDraft>('/plugin-marketplaces/preview', 'POST', request, {timeout: 120_000, signal}))} />
    <section className="space-y-3 rounded-xl border border-line bg-raised/20 p-4" aria-label={t('ui.extensions.installPlugin')}>
      <div><h4 className="text-sm font-medium text-ink">{t('ui.extensions.installPlugin')}</h4><p className="mt-1 text-xs text-muted">{t('ui.extensions.localHint')}</p></div>
      <DirectoryField value={path} onChange={value => {discardPreview(); setPath(value)}} label={t('ui.extensions.sourceDirectory')} disabled={!!busy} />
      <div className="flex flex-wrap gap-2"><Button size="sm" disabled={!path.trim() || !!busy} loading={busy === 'preview'} icon={<Search className="h-3.5 w-3.5" />} onClick={() => void previewSource(path.trim())}>{t('ui.extensions.preview')}</Button>
      <Button size="sm" variant="secondary" disabled={!path.trim() || !!busy} onClick={() => usePluginSourceDrafts.getState().open(path.trim())}>{t('ui.extensions.editSource')}</Button></div>
      {preview && <div className="space-y-3 border-t border-line pt-3">
        <PackageSummary item={preview} />
        {preview.update ? <UpdateReview item={preview} /> : <p className="text-2xs text-muted">{t('ui.extensions.noAutomaticConnection')}</p>}
        <Button size="sm" loading={busy === 'install'} disabled={!!busy || (preview.update ? data?.packages.find(item => item.name === preview.name)?.digest !== preview.update.current_digest || preview.digest === preview.update.current_digest : data?.packages.some(item => item.name === preview.name))} icon={<Download className="h-3.5 w-3.5" />} onClick={() => void perform('install', async () => {
          if (!reviewed) return
          const endpoint = preview.update ? '/plugin-packages/' + encodeURIComponent(preview.name) + '/update' : '/plugin-packages/install'
          const body = preview.update ? { path: reviewed.path, digest: preview.digest, current_digest: preview.update.current_digest } : { path: reviewed.path, digest: preview.digest }
          await runtimeRequest(endpoint, 'POST', body, { timeout: 120_000 })
          if (mounted.current) { setReviewed(null); setPath('') }
        }, {key: preview.update ? 'ui.extensions.updated' : 'ui.extensions.installed'})}>{t(preview.update ? 'ui.extensions.update' : 'ui.extensions.install')}</Button>
      </div>}
    </section>
    {editorRoot && <PluginSourceEditor key={editorRoot} root={editorRoot} onChanged={discardPreview} onReview={source => void previewSource(source)} />}
    <div className="ui-input-group flex items-center gap-2 rounded-lg border border-line px-3"><Search className="h-3.5 w-3.5 text-faint" /><input type="search" value={query} onChange={event => setQuery(event.target.value)} aria-label={t('ui.extensions.searchPlugins')} placeholder={t('ui.extensions.searchPlugins')} className="h-9 min-w-0 flex-1 bg-transparent text-xs outline-none" /><span className="text-2xs tabular-nums text-faint">{data?.packages.length ?? 0}</span></div>
    {!data && !error ? <p role="status" className="flex items-center gap-2 text-xs text-muted"><Spinner className="h-3.5 w-3.5" />{t('ui.extensions.loading')}</p> : visible.length ? <div className="divide-y divide-line">
      {visible.map(item => <article key={item.name} className="space-y-3 py-4">
        <div className="flex items-start gap-3"><div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-raised text-muted"><Package className="h-5 w-5" /></div><div className="min-w-0 flex-1"><PackageSummary item={item} /><div className="mt-2 flex flex-wrap items-center gap-2"><Badge tone={item.enabled ? 'ok' : 'neutral'}>{t(item.enabled ? 'ui.extensions.enabled' : 'ui.extensions.disabled')}</Badge><span className="text-2xs text-muted">{t('ui.extensions.connectionCount', { count: item.connectors.filter(connector => connector.connected).length, total: item.connectors.length })}</span></div></div></div>
        <PluginAppReferences item={item} busy={!!busy} onLink={(alias, connector) => void perform('app:' + item.name + ':' + alias, () => runtimeRequest('/plugin-packages/' + encodeURIComponent(item.name) + '/apps/' + encodeURIComponent(alias) + '/link', 'POST', { connector }))} />
        <PluginHookReview item={item} busy={!!busy} onTrust={() => void perform("hooks:" + item.name, () => runtimeRequest("/plugin-packages/" + encodeURIComponent(item.name) + "/hooks/trust", "POST", { fingerprint: item.hooks_fingerprint, trusted: !item.hooks_trusted }))} />
        <div className="flex flex-wrap gap-2">
          {item.path && <Button size="sm" variant="secondary" disabled={!!busy} onClick={() => usePluginSourceDrafts.getState().open(item.path!)}>{t('ui.extensions.viewInstalledFiles')}</Button>}
          <Button size="sm" disabled={!!busy} icon={<Power className="h-3.5 w-3.5" />} aria-pressed={item.enabled} onClick={() => void perform(item.name, () => runtimeRequest('/plugin-packages/' + encodeURIComponent(item.name) + '/enabled', 'POST', { enabled: !item.enabled }, { timeout: 120_000 }))}>{t(item.enabled ? 'ui.extensions.disable' : 'ui.extensions.enable')}</Button>
          {item.connectors.length > 0 && <Button size="sm" variant="secondary" disabled={!!busy} onClick={() => useAppStore.getState().setSettingsOpen(true, 'connectors')}>{t('ui.extensions.manageConnectors')}</Button>}
          {removing === item.name ? <><Button size="sm" variant="danger" disabled={!!busy} onClick={() => void perform('remove:' + item.name, async () => {
            await runtimeRequest('/plugin-packages/' + encodeURIComponent(item.name), 'DELETE', undefined, { timeout: 120_000 })
            if (mounted.current) setRemoving(null)
          }, {key: 'ui.extensions.removed'})}>{t('ui.extensions.confirmRemove')}</Button><Button size="sm" variant="ghost" disabled={!!busy} onClick={() => setRemoving(null)}>{t('ui.extensions.cancel')}</Button></> : <Button size="sm" variant="ghost" disabled={!!busy} icon={<Trash2 className="h-3.5 w-3.5" />} onClick={() => setRemoving(item.name)}>{t('ui.extensions.remove')}</Button>}
        </div>
      </article>)}
    </div> : <EmptyHint icon={<Package className="h-5 w-5" />} title={t(query ? 'ui.extensions.noMatches' : 'ui.extensions.noPlugins')} description={t(query ? 'ui.extensions.noMatchesHint' : 'ui.extensions.noPluginsHint')} />}
  </div>
}

function UpdateReview({ item }: { item: PluginPackage }) {
  useLocale()
  const review = item.update
  if (!review) return null
  return <section aria-label={t('ui.extensions.updateReview')} className="space-y-2 rounded-lg border border-line p-3">
    <p className="text-xs font-medium text-ink">{t('ui.extensions.updateVersion', { before: review.current_version, after: item.version })}</p>
    <p className="text-xs leading-relaxed text-muted">{t('ui.extensions.updateHint')}</p>
    {review.changed_connectors.length > 0 && <p className="break-words text-xs text-warn">{t('ui.extensions.changedServices', { names: review.changed_connectors.join(', ') })}</p>}
    {review.removed_connectors.length > 0 && <p className="break-words text-xs text-muted">{t('ui.extensions.removedServices', { names: review.removed_connectors.join(', ') })}</p>}
    {review.removed_skills.length > 0 && <p className="break-words text-xs text-muted">{t('ui.extensions.removedSkills', { names: review.removed_skills.join(', ') })}</p>}
    {review.hooks_changed && <p className="text-xs text-warn">{t('ui.extensions.changedHooks')}</p>}
  </section>
}

function PackageSummary({ item }: { item: PluginPackage }) {
  return <div className="min-w-0 space-y-1.5">
    <div className="flex flex-wrap items-center gap-2"><h4 className="break-words text-sm font-medium text-ink">{item.display_name}</h4><span className="font-mono text-2xs text-faint">{item.version}</span></div>
    {item.description && <p className="max-w-prose break-words text-xs leading-relaxed text-muted">{item.description}</p>}
    <div className="flex flex-wrap gap-1.5">{item.skills.map(skill => <Badge key={skill.name}>{skill.name}</Badge>)}{item.connectors.map(connector => <Badge key={connector.name} tone="brand">{connector.name}</Badge>)}{item.apps?.map(app => <Badge key={app.name}>{app.name} · {t(app.required ? 'ui.extensions.requiredApp' : 'ui.extensions.optionalApp')}</Badge>)}</div>
  </div>
}

function PluginAppReferences({ item, busy, onLink }: { item: PluginPackage; busy: boolean; onLink: (alias: string, connector: string) => void }) {
  useLocale()
  if (!item.apps?.length) return null
  return <section className="space-y-2" aria-label={t('ui.extensions.registeredApps')}>
    <p className="text-xs leading-relaxed text-muted">{t('ui.extensions.appHint')}</p>
    {item.apps.map(app => <AppReference key={JSON.stringify([app.name, app.id])} app={app} busy={busy} onLink={connector => onLink(app.name, connector)} />)}
    <Button size="sm" variant="secondary" onClick={() => useAppStore.getState().setSettingsOpen(true, 'connectors')}>{t('ui.extensions.manageConnectors')}</Button>
  </section>
}

function AppReference({ app, busy, onLink }: { app: PluginAppReference; busy: boolean; onLink: (connector: string) => void }) {
  const [selected, setSelected] = useState('')
  const candidates = app.candidates ?? []
  const value = candidates.includes(selected) ? selected : app.connectors?.find(name => candidates.includes(name)) ?? candidates[0] ?? ''
  return <div className="space-y-2 rounded-lg border border-line p-3">
    <div className="flex flex-wrap items-center gap-2"><span className="text-xs font-medium text-ink">{app.name}</span><Badge tone={app.connected ? 'ok' : 'neutral'}>{t('ui.extensions.appStatus.' + (app.status ?? 'unavailable'))}</Badge></div>
    <p className="break-all font-mono text-2xs text-muted">{app.id}</p>
    <div className="flex flex-wrap gap-2">
      <select className="ui-field min-w-0 flex-1 text-xs" aria-label={t('ui.extensions.appConnector', { name: app.name })} disabled={busy || !app.candidates?.length} value={value} onChange={event => setSelected(event.target.value)}>
        {!app.candidates?.length && <option value="">{t('ui.extensions.noAppConnector')}</option>}
        {app.candidates?.map(name => <option key={name} value={name}>{name}</option>)}
      </select>
      <Button size="sm" disabled={busy || !value || !candidates.includes(value) || app.connectors?.includes(value)} onClick={() => onLink(value)}>{t('ui.extensions.linkApp')}</Button>
    </div>
  </div>
}
