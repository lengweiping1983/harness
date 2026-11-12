import { ConfirmRemoval } from '@/components/ui/ConfirmRemoval'
import { useCallback, useEffect, useRef, useState } from 'react'
import { CirclePlus, Github, Plug } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { pluginRequest } from '@/lib/http'
import type { ConnectorRow, Overview, PresetRow } from '@/lib/extensionCatalog'
import { useAppStore } from '@/store/app'
import { usePageVisible } from '@/hooks/usePageVisible'
import { Badge, Button, EmptyHint, Section, Spinner } from '@/components/ui/primitives'
import { CatalogToolbar, InstallDisclosure, Switch, field, matchesCatalog } from './ExtensionCatalogControls'

function connectorStatus(connector: ConnectorRow): { tone: 'ok' | 'brand' | 'warn' | 'neutral'; label: string } {
  if (connector.oauth?.state === 'pending') return { tone: 'brand', label: t("ui.components.overlays.EscPanel.b4e41afe6828") }
  if (!connector.enabled) return { tone: 'neutral', label: t("ui.components.overlays.EscPanel.a8c3698b5b8c") }
  if (connector.connected) return { tone: 'ok', label: t("ui.components.overlays.EscPanel.212aa8951464", {p0: (connector.tool_count)}) }
  if (connector.bound) return { tone: 'brand', label: connector.oauth?.state === 'authorized' ? t("ui.components.overlays.EscPanel.e4616dbe7b06") : t("ui.components.overlays.EscPanel.0cc8a5bab03c") }
  return { tone: 'warn', label: connector.oauth?.state === 'authorized' ? t("ui.components.overlays.EscPanel.0eaa9b861da1") : t("ui.components.overlays.EscPanel.e026c6693dc5") }
}

function connectorOAuth(connector: ConnectorRow, local: Record<string, ConnectorRow['oauth']>): ConnectorRow['oauth'] {
  return (Object.hasOwn(local, connector.name) ? local[connector.name] : undefined) ?? connector.oauth
}

export function ConnectorsTab({
  overview,
  envMcp,
  busy,
  perform,
}: {
  overview: Overview
  envMcp: Array<{ server_name: string; transport: string }>
  busy: string
  perform: (key: string, op: () => Promise<unknown>, receipt: string) => Promise<boolean>
}) {
  useLocale()
  const visible = usePageVisible()
  const mounted = useRef(false)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  const [name, setName] = useState('')
  const [recommendationsOpen, setRecommendationsOpen] = useState(() => overview.connectors.length === 0)
  const [transport, setTransport] = useState<'stdio' | 'http'>('stdio')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState('')
  const [url, setUrl] = useState('')
  const nameInput = useRef<HTMLInputElement>(null)
  const formRevision = useRef(0)
  const [presets, setPresets] = useState<PresetRow[]>([])
  const [query, setQuery] = useState('')
  const filteredConnectors = overview.connectors.filter(connector => matchesCatalog(query, [connector.name, connector.metadata?.display_name, connector.metadata?.description, connector.url, connector.command]))
  // OAuth 状态本地镜像：目录快照之外的实时态（pending → authorized/error）
  const [oauthLocal, setOauthLocal] = useState<Record<string, ConnectorRow['oauth']>>({})

  const loadPresets = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await pluginRequest<{ presets: PresetRow[] }>('/connectors/presets', 'GET', undefined, { signal })
      if (mounted.current && !signal?.aborted) setPresets(data.presets ?? [])
    } catch {
      if (mounted.current && !signal?.aborted) setPresets([]) // 预设目录不可用时静默降级为不展示推荐区
    }
  }, [])

  useEffect(() => {
    if (!visible || !overview.enabled.connectors) return
    const controller = new AbortController()
    void loadPresets(controller.signal)
    return () => controller.abort()
  }, [loadPresets, visible, overview.enabled.connectors])

  // 刷新后的后端目录是权威状态，不能被旧轮询镜像永久遮蔽。
  useEffect(() => { setOauthLocal(current => Object.keys(current).length ? {} : current) }, [overview.connectors])

  const pendingKey = JSON.stringify(overview.connectors.filter(
    connector => connectorOAuth(connector, oauthLocal)?.state === 'pending',
  ).map(connector => connector.name))

  useEffect(() => {
    if (!visible || busy || !overview.enabled.connectors) return
    const pending: string[] = JSON.parse(pendingKey)
    if (!pending.length) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      const updates: Record<string, ConnectorRow['oauth']> = Object.create(null)
      await Promise.all(pending.map(async (name) => {
        try {
          const status = await pluginRequest<ConnectorRow['oauth']>(
            '/connectors/' + encodeURIComponent(name) + '/oauth/status',
            'GET', undefined, { signal: controller.signal },
          )
          if (!controller.signal.aborted) updates[name] = status
        } catch {
          // 未确认的读取失败不修改授权状态；下一轮继续读取。
        }
      }))
      if (!controller.signal.aborted) {
        // 整批读取结束后再发布，避免先完成的状态改变 pendingKey 并取消、重读同批其他连接。
        if (Object.keys(updates).length) setOauthLocal(prev => ({ ...prev, ...updates }))
        timer = setTimeout(() => void poll(), 2000)
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [overview.connectors, pendingKey, visible, busy, overview.enabled.connectors])

  if (!overview.enabled.connectors) {
    return <EmptyHint icon={<Plug className="h-4 w-4" />} title={t("ui.components.overlays.EscPanel.e944e835a38d")} description={t("ui.components.overlays.EscPanel.bd04d98894a4")} />
  }

  const pendingPresets = presets.filter((preset) => !preset.installed)

  return (
    <div className="space-y-3">
      <CatalogToolbar query={query} onQuery={setQuery} count={filteredConnectors.length} total={overview.connectors.length} label={t('ui.extensions.searchConnectors')} />

      {pendingPresets.length ? (
        <details open={recommendationsOpen} onToggle={event => setRecommendationsOpen(event.currentTarget.open)} className="rounded-lg border border-line p-3">
          <summary className="cursor-pointer text-xs font-medium">{t("ui.components.overlays.EscPanel.37122de91b3c")}</summary>
          <div className="mt-3 space-y-2">
          {pendingPresets.map((preset) => {
            const key = 'preset:' + preset.name
            return (
              <div key={preset.name} className="rounded-lg border border-line px-3 py-2.5">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-ink">
                      {preset.name === 'github' ? <Github className="h-3.5 w-3.5" /> : <Plug className="h-3.5 w-3.5" />}
                      {preset.name}
                      <Badge>{preset.transport}</Badge>
                      {preset.has_skill ? <Badge tone="brand">{t("ui.components.overlays.EscPanel.834c57fe8ed3")}</Badge> : null}
                    </p>
                    <p className="mt-0.5 text-2xs text-muted">{preset.description}</p>
                    {preset.auth_hint ? (
                      <p className="mt-0.5 text-2xs text-faint">{preset.auth_hint}</p>
                    ) : null}
                  </div>
                  <Button
                    size="xs"
                    disabled={busy === key}
                    onClick={() =>
                      void perform(
                        key,
                        async () => {
                          await pluginRequest('/connectors/presets/' + encodeURIComponent(preset.name), 'POST')
                          await loadPresets()
                        },
                        t("ui.components.overlays.EscPanel.bd8524a746fa", {p0: (preset.name)}),
                      )
                    }
                  >
                    {busy === key ? <Spinner className="h-3 w-3" /> : <CirclePlus className="h-3 w-3" />}{t("ui.components.overlays.EscPanel.343a7655ae60")}</Button>
                </div>
              </div>
            )
          })}
          </div>
        </details>
      ) : null}

      {filteredConnectors.length === 0 ? (
        <EmptyHint
          icon={<Plug className="h-4 w-4" />}
          title={t(overview.connectors.length ? 'ui.connectors.noMatches' : 'ui.components.overlays.EscPanel.c0b1a386e295')}
          description={t(overview.connectors.length ? 'ui.connectors.noMatchesHint' : 'ui.components.overlays.EscPanel.b322bdce8ba9')}
        />
      ) : (
        filteredConnectors.map((raw) => {
          // 实时 OAuth 状态优先于目录快照
          const connector = { ...raw, oauth: connectorOAuth(raw, oauthLocal) }
          const oauth = connector.oauth
          const key = 'connector:' + connector.name
          const status = connectorStatus(connector)
          return (
            <Section key={connector.name} className="rounded-xl border border-line px-3 py-2.5">
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-ink">
                    {connector.metadata?.display_name ?? connector.name}
                    <Badge>{connector.transport}</Badge>
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </p>
                  <p className="mt-0.5 text-2xs text-faint [overflow-wrap:anywhere]">
                    {connector.transport === 'stdio'
                      ? [connector.command, ...(connector.args ?? [])].filter(Boolean).join(' ')
                      : connector.url}
                  </p>
                  {connector.metadata?.description ? (
                    <p className="mt-0.5 text-2xs text-muted">{connector.metadata.description}</p>
                  ) : null}
                  {!connector.bound && connector.metadata?.auth_hint ? (
                    <p className="mt-0.5 text-2xs text-warn">{connector.metadata.auth_hint}</p>
                  ) : null}
                  {connector.last_error ? (
                    <p role="alert" className="mt-0.5 text-2xs text-danger [overflow-wrap:anywhere]">
                      {connector.last_error}
                    </p>
                  ) : null}
                  {oauth?.state === 'error' && oauth.message ? (
                    <p role="alert" className="mt-0.5 text-2xs text-danger [overflow-wrap:anywhere]">
                      {oauth.message}
                    </p>
                  ) : null}
                </div>
                <Switch
                  checked={connector.enabled}
                  disabled={busy === key + ':toggle' || connector.metadata?.plugin_enabled === false}
                  onChange={(next) =>
                    void perform(
                      key + ':toggle',
                      () => pluginRequest('/connectors/' + encodeURIComponent(connector.name) + '/enabled', 'POST', { enabled: next }),
                      next ? t("ui.components.overlays.EscPanel.17d2594b6241", {p0: (connector.name)}) : t("ui.components.overlays.EscPanel.dad663a8630a", {p0: (connector.name)}),
                    )
                  }
                  label={t(connector.enabled ? 'ui.catalog.disableNamed' : 'ui.catalog.enableNamed', { name: connector.name })}
                />
              </div>
              {oauth?.state === 'pending' ? (
                <div className="mt-2 rounded-lg border border-brand/30 bg-brand/5 px-3 py-2">
                  <div className="flex items-center gap-1.5 text-2xs text-brand">
                    <Spinner className="h-3 w-3" />
                    {oauth.mode === 'code'
                      ? t("ui.components.overlays.EscPanel.b0358e94f40a")
                      : oauth.user_code
                        ? t("ui.components.overlays.EscPanel.1135f14bdd69")
                        : t("ui.components.overlays.EscPanel.0e577124fffd")}
                  </div>
                  {oauth.user_code ? (
                    <div className="mt-1.5 flex flex-wrap items-center gap-2">
                      <code className="rounded bg-canvas px-2 py-1 font-mono text-xs text-ink">{oauth.user_code}</code>
                      {oauth.verification_uri ? (
                        <a
                          className="text-2xs text-brand underline"
                          href={oauth.verification_uri}
                          target="_blank"
                          rel="noreferrer"
                        >{t("ui.components.overlays.EscPanel.1e40194e775f")}</a>
                      ) : null}
                    </div>
                  ) : null}
                  {!oauth.user_code && oauth.authorize_url ? (
                    <a
                      className="mt-1 inline-block text-2xs text-brand underline"
                      href={oauth.authorize_url}
                      target="_blank"
                      rel="noreferrer"
                    >{t("ui.components.overlays.EscPanel.df06ab102680")}</a>
                  ) : null}
                </div>
              ) : null}
              <div className="flex flex-wrap gap-2">
                {connector.metadata?.plugin_id ? <Button size="xs" variant="secondary" onClick={() => useAppStore.getState().setSettingsOpen(true, 'plugins')}>{t('ui.extensions.managePlugin')}</Button> : null}
                {connector.enabled ? (
                  connector.bound ? (
                    <Button
                      size="xs"
                      variant="secondary"
                      disabled={busy === key + ':unbind'}
                      onClick={() =>
                        void perform(
                          key + ':unbind',
                          () => pluginRequest('/connectors/' + encodeURIComponent(connector.name) + '/unbind', 'POST'),
                          t("ui.components.overlays.EscPanel.bf7115137003", {p0: (connector.name)}),
                        )
                      }
                    >{t("ui.components.overlays.EscPanel.3ba0a291df80")}</Button>
                  ) : (
                    <Button
                      size="xs"
                      disabled={busy === key + ':bind'}
                      onClick={() =>
                        void perform(
                          key + ':bind',
                          () => pluginRequest('/connectors/' + encodeURIComponent(connector.name) + '/bind', 'POST'),
                          t("ui.components.overlays.EscPanel.38308a81afdd", {p0: (connector.name)}),
                        )
                      }
                    >{t("ui.components.overlays.EscPanel.87170dfefacc")}</Button>
                  )
                ) : null}
                {oauth?.supported && oauth.state !== 'authorized' ? (
                  <Button
                    size="xs"
                    variant="secondary"
                    disabled={busy === key + ':oauth' || oauth.state === 'pending'}
                    onClick={() =>
                      void perform(
                        key + ':oauth',
                        async () => {
                          const status = await pluginRequest<ConnectorRow['oauth']>(
                            '/connectors/' + encodeURIComponent(connector.name) + '/oauth/login',
                            'POST',
                          )
                          if (mounted.current) setOauthLocal((prev) => ({ ...prev, [connector.name]: status }))
                        },
                        t("ui.components.overlays.EscPanel.acbed3701023", {p0: (connector.name)}),
                      )
                    }
                  >
                    {oauth.state === 'pending' ? <Spinner className="h-3 w-3" /> : null}
                    {oauth.state === 'error' ? t("ui.components.overlays.EscPanel.b98b4801c12a") : t("ui.components.overlays.EscPanel.24bbeda1b52f")}
                  </Button>
                ) : oauth?.state === 'authorized' ? (
                  <Button
                    size="xs"
                    variant="secondary"
                    disabled={busy === key + ':oauth'}
                    onClick={() =>
                      void perform(
                        key + ':oauth',
                        async () => {
                          await pluginRequest('/connectors/' + encodeURIComponent(connector.name) + '/oauth', 'DELETE')
                          if (mounted.current) setOauthLocal((prev) => {
                            const next = { ...prev }
                            delete next[connector.name]
                            return next
                          })
                        },
                        t("ui.components.overlays.EscPanel.9d45e5dfae95", {p0: (connector.name)}),
                      )
                    }
                  >{t("ui.components.overlays.EscPanel.4f3708bc0266")}</Button>
                ) : null}
                {!connector.metadata?.plugin_id && <ConfirmRemoval key={connector.name} name={connector.name}
                  prompt={t('ui.catalog.removeConnectorConfirm', { name: connector.name })} disabled={!!busy}
                  onConfirm={() => perform(key + ':remove',
                    () => pluginRequest('/connectors/' + encodeURIComponent(connector.name), 'DELETE'),
                    t("ui.components.overlays.EscPanel.0bafa8628c1c", {p0: connector.name}))} />}
              </div>
            </Section>
          )
        })
      )}

      {envMcp.length ? (
        <Section title={t("ui.components.overlays.EscPanel.2f9a30d1a82e")}>
          {envMcp.map((server) => (
            <div key={server.server_name} className="flex items-center gap-2 rounded-lg border border-line px-3 py-2">
              <span className="min-w-0 flex-1 truncate text-xs text-ink">{server.server_name}</span>
              <Badge>{server.transport}</Badge>
              <span className="text-2xs text-faint">{t("ui.components.overlays.EscPanel.0046357f878a")}</span>
            </div>
          ))}
        </Section>
      ) : null}

      <InstallDisclosure title={t("ui.components.overlays.EscPanel.3f211352cd01")}>
        {(close) => (
          <>
            <input
              ref={nameInput}
              className={field}
              value={name}
              onChange={(event) => { formRevision.current++; setName(event.target.value) }}
              placeholder={t("ui.components.overlays.EscPanel.74ca66433b97")}
              aria-label={t("ui.components.overlays.EscPanel.c925ddb9eb8a")}
            />
            <select
              className={field}
              value={transport}
              onChange={(event) => { formRevision.current++; setTransport(event.target.value as 'stdio' | 'http') }}
              aria-label={t("ui.components.overlays.EscPanel.84dada05d8d5")}
            >
              <option value="stdio">{t("ui.components.overlays.EscPanel.e0481ca7aab6")}</option>
              <option value="http">{t("ui.components.overlays.EscPanel.00e4879dc460")}</option>
            </select>
            {transport === 'stdio' ? (
              <>
                <input
                  className={field}
                  value={command}
                  onChange={(event) => { formRevision.current++; setCommand(event.target.value) }}
                  placeholder={t("ui.components.overlays.EscPanel.00a0b4d1c4a6")}
                  aria-label={t("ui.components.overlays.EscPanel.8b76184a7d6e")}
                />
                <input
                  className={field}
                  value={args}
                  onChange={(event) => { formRevision.current++; setArgs(event.target.value) }}
                  placeholder={t("ui.components.overlays.EscPanel.8dcccd2e3dbf")}
                  aria-label={t("ui.components.overlays.EscPanel.570674fea082")}
                />
              </>
            ) : (
              <input
                className={field}
                value={url}
                onChange={(event) => { formRevision.current++; setUrl(event.target.value) }}
                placeholder={t("ui.components.overlays.EscPanel.f76dad2d7096")}
                aria-label={t("ui.components.overlays.EscPanel.02739d1bae8b")}
              />
            )}
            <Button
              size="xs"
              disabled={
                !name.trim() ||
                (transport === 'stdio' ? !command.trim() : !url.trim()) ||
                busy === 'connector:add'
              }
              onClick={() =>
                void perform(
                  'connector:add',
                  async () => {
                    const submittedForm = nameInput.current
                    const submittedRevision = formRevision.current
                    await pluginRequest('/connectors', 'POST', {
                      name: name.trim(),
                      transport,
                      command: transport === 'stdio' ? command.trim() : undefined,
                      args: transport === 'stdio' ? args.trim().split(/\s+/).filter(Boolean) : [],
                      url: transport === 'http' ? url.trim() : undefined,
                      enabled: true,
                      bound: false,
                    })
                    if (!mounted.current || nameInput.current !== submittedForm || formRevision.current !== submittedRevision) return
                    setName('')
                    setCommand('')
                    setArgs('')
                    setUrl('')
                    close()
                  },
                  t("ui.components.overlays.EscPanel.e180ad201129"),
                )
              }
            >{t("ui.components.overlays.EscPanel.7a8a11ead507")}</Button>
          </>
        )}
      </InstallDisclosure>
    </div>
  )
}
