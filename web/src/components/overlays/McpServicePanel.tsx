import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { usePageVisible } from '@/hooks/usePageVisible'
import { useEffect, useRef, useState } from 'react'
import { Copy } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { mcpServicePassword, mcpServiceStart, mcpServiceStatus, mcpServiceStop, type McpServiceState } from '@/lib/api'
import { Button } from '@/components/ui/primitives'
import { SecretInput } from '@/components/ui/SecretInput'
import {McpCompanionPanel} from './McpCompanionPanel'
import {parseInteger, validTunnelUrl} from '@/lib/settingsValidation'

const statusKeys = { stopped: 'ui.mcpService.stopped', starting: 'ui.mcpService.starting', running: 'ui.mcpService.running', stopping: 'ui.mcpService.stopping', failed: 'ui.mcpService.failed' }

export function McpServicePanel() {
  useLocale()
  const visible = usePageVisible()
  const [state, setState] = useState<McpServiceState | null>(null)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [secretBusy, setSecretBusy] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [readError, setReadError] = useState<ResourceText>('')
  const [notice, setNotice] = useState<ResourceText>('')
  const [workspaces, setWorkspaces] = useState<NonNullable<McpServiceState['workspaces']>>([])
  const mounted = useRef(false)
  const generation = useRef(0)
  const working = useRef(false)
  const companionWorking = useRef(false)
  const companionSave = useRef<Promise<boolean> | null>(null)
  const edited = useRef(false)
  const [mode, setMode] = useState<'quick' | 'named'>('quick')
  const [publicUrl, setPublicUrl] = useState('')
  const [originPort, setOriginPort] = useState('12345')
  const [token, setToken] = useState('')
  function hydrate(result: McpServiceState) {
    if (!edited.current && result.tunnel) {
      setMode(result.tunnel.mode); setPublicUrl(result.tunnel.public_url); setOriginPort(String(result.tunnel.origin_port))
    }
  }

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; generation.current++ }
  }, [])

  useEffect(() => {
    if (!visible) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      const current = generation.current
      try {
        if (working.current || companionWorking.current) return
        const result = await mcpServiceStatus(controller.signal)
        if (controller.signal.aborted || !mounted.current || current !== generation.current) return
        if (result.ok) {
          setState(result.data); hydrate(result.data)
          setWorkspaces(result.data.workspaces || [])
          setReadError('')
          if (result.data.status !== 'running') { generation.current++; setPassword('') }
        } else setReadError(result.copy ?? result.message)
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void refresh(), 2000)
      }
    }
    void refresh()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [visible])

  async function change(action: 'start' | 'stop') {
    if (working.current) return
    if (action === 'start' && (parseInteger(originPort, 1024, 65535) === undefined
      || token.length > 8192 || /\s/.test(token)
      || mode === 'named' && (!validTunnelUrl(publicUrl) || !token && !state?.tunnel?.has_token))) {
      setError({key: 'ui.mcpService.error.tunnelConfig'})
      return
    }
    working.current = true
    setBusy(true); setError(''); setReadError(''); setNotice(''); setPassword(''); generation.current++
    try {
      const pending = companionSave.current
      if (pending) {
        const saved = await pending
        if (companionSave.current === pending) companionSave.current = null
        if (!mounted.current || (!saved && action === 'start')) return
      }
      const settings = {mode, public_url: publicUrl, origin_port: Number(originPort), ...(token ? {token} : {})}
      const result = await (action === 'stop' ? mcpServiceStop() : mcpServiceStart(settings))
      if (!mounted.current) return
      if (result.ok) { edited.current = false; setToken(''); hydrate(result.data); setState(result.data); if (result.data.workspaces) setWorkspaces(result.data.workspaces) }
      else setError(result.copy ?? result.message)
    } finally { working.current = false; if (mounted.current) setBusy(false) }
  }

  async function copy(value: string) {
    try { await navigator.clipboard.writeText(value); if (mounted.current) setNotice({key: 'ui.mcpService.copied'}) }
    catch { if (mounted.current) setError({key: 'ui.mcpService.copyFailed'}) }
  }

  async function secret(show: boolean) {
    if (show && password) { setPassword(''); generation.current++; return }
    const current = generation.current
    setSecretBusy(true); setError(''); setNotice('')
    try {
      const result = await mcpServicePassword()
      if (!mounted.current || current !== generation.current) return
      if (!result.ok) { setError(result.copy ?? result.message); return }
      if (show) setPassword(result.data.password)
      else await copy(result.data.password)
    } finally { if (mounted.current) setSecretBusy(false) }
  }

  const running = state?.status === 'running'
  const cooling = mode === 'quick' && (state?.quick_retry_at || 0) > Date.now() / 1000
  const changing = busy || state?.status === 'starting' || state?.status === 'stopping'
  return <section className="space-y-5">
    <div><h3 className="ui-section-title">{t('ui.mcpService.title')}</h3><p className="mt-2 text-sm leading-relaxed text-muted">{t('ui.mcpService.description')}</p></div>
    <div className="rounded-xl border border-line bg-raised/30 p-4 space-y-4">
      <div className="space-y-2"><p className="text-sm font-medium text-ink">{t('ui.mcpService.allProjects')}</p><p className="text-xs leading-relaxed text-muted">{t('ui.mcpService.autoProjects')}</p>{running && state.project_scope === 'selected' && <p role="status" className="text-xs text-warn">{t('ui.mcpService.scopeNotice')}</p>}<details className="text-xs text-muted"><summary className="cursor-pointer">{t('ui.mcpService.projectCount', { count: workspaces.length })}</summary><ul className="mt-3 space-y-2">{workspaces.map(row => <li key={row.id} className="break-all"><span className="font-medium text-ink">{row.name}</span><span className="ml-2 text-faint">{row.path}</span></li>)}</ul></details></div>
      <fieldset disabled={changing || running} className="space-y-3 min-w-0">
        <label className="block text-sm text-ink">{t('ui.mcpService.tunnelMode')}
          <select aria-label={t('ui.mcpService.tunnelMode')} value={mode} onChange={event => { edited.current = true; setMode(event.target.value as 'quick' | 'named') }} className="mt-2 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink">
            <option value="quick">{t('ui.mcpService.quickMode')}</option><option value="named">{t('ui.mcpService.namedMode')}</option>
          </select>
        </label>
        {mode === 'quick' ? <p className="text-xs leading-relaxed text-muted">{t('ui.mcpService.quickWarning')}</p> : <>
          <p className="text-xs leading-relaxed text-muted">{t('ui.mcpService.namedSetup')} <a className="text-brand underline" href="https://developers.cloudflare.com/tunnel/get-started/" target="_blank" rel="noreferrer">{t('ui.mcpService.setupGuide')}</a></p>
          <label className="block text-sm text-ink">{t('ui.mcpService.hostname')}<input type="url" value={publicUrl} onChange={event => { edited.current = true; setPublicUrl(event.target.value) }} placeholder="https://mcp.example.com" className="mt-2 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink" /></label>
          <label className="block text-sm text-ink">{t('ui.mcpService.originPort')}<input type="number" min="1024" max="65535" step="1" value={originPort} onChange={event => { edited.current = true; setOriginPort(event.target.value) }} className="mt-2 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink" /></label>
          <p className="text-xs leading-relaxed text-muted">{t('ui.mcpService.originHint', {port: originPort})}</p>
          <label className="block text-sm text-ink">{t('ui.mcpService.tunnelToken')}<SecretInput containerClassName="mt-2" autoComplete="new-password" spellCheck={false} value={token} placeholder={state?.tunnel?.token_mask ?? ''} onChange={event => { edited.current = true; setToken(event.target.value) }} className="bg-surface text-sm" /></label>
          {!state?.tunnel?.has_token && <p className="text-xs leading-relaxed text-muted">{t('ui.mcpService.tokenPrivate')}</p>}
        </>}
      </fieldset>
      <p className="text-xs text-muted">{t('ui.mcpService.savedHint')}</p>
      {cooling && <p role="status" className="text-sm text-warn">{t('ui.mcpService.cooldown')}</p>}
      {state?.reconnecting && <p role="status" className="text-sm text-warn">{t('ui.mcpService.reconnecting')}</p>}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span role="status" className="text-sm text-muted">{state ? t(state.status === 'starting' && state.tunnel?.mode === 'named' ? 'ui.mcpService.startingNamed' : statusKeys[state.status]) : t('ui.mcpService.loading')}</span>
          <Button size="sm" variant={running ? 'secondary' : 'primary'} disabled={!state || busy || state.status === 'stopping' || (!running && state.status !== 'starting' && cooling)} loading={busy || state?.status === 'stopping'} onClick={() => void change(running || state?.status === 'starting' ? 'stop' : 'start')}>{running || state?.status === 'starting' ? t('ui.mcpService.stop') : t('ui.mcpService.start')}</Button>
      </div>
      {running && <div className="space-y-4">
        {([['ui.mcpService.local', state.local_url], [state.tunnel?.mode === 'named' ? 'ui.mcpService.publicNamed' : 'ui.mcpService.public', state.public_url]] as const).map(([label, url]) => <div key={label}><p className="mb-2 text-sm font-medium text-ink">{t(label)}</p><div className="flex items-center gap-2"><input aria-label={t(label)} readOnly value={url} className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink" /><Button size="sm" variant="secondary" onClick={() => void copy(url)} aria-label={t('ui.mcpService.copyAddress')}><Copy size={14} />{t('ui.mcpService.copy')}</Button></div></div>)}
        <div><p className="mb-2 text-sm font-medium text-ink">{t('ui.mcpService.password')}</p><div className="flex items-center gap-2"><SecretInput containerClassName="flex-1" aria-label={t('ui.mcpService.password')} readOnly autoComplete="off" value={password || state.password_mask || ''} className="bg-surface text-sm" concealedType="text" revealed={!!password} toggleDisabled={secretBusy} toggleBusy={secretBusy} onToggleReveal={() => void secret(true)} /><Button size="sm" variant="secondary" disabled={secretBusy} onClick={() => void secret(false)}><Copy size={14} />{t('ui.mcpService.copy')}</Button></div></div>
        <p className="text-xs leading-relaxed text-muted">{t('ui.mcpService.connectionHint')}</p>
        <p className="text-xs leading-relaxed text-muted">{t('ui.mcpService.lifecycleHint')}</p>
        {state?.tunnel?.mode !== 'named' && <p className="text-xs leading-relaxed text-muted">{t('ui.mcpService.temporaryHint')}</p>}
      </div>}
      {(error || readError || state?.error) && <p role="alert" className={`text-sm break-words ${state?.error_copy?.key === 'ui.mcpService.error.rateLimited' ? 'text-warn' : 'text-danger'}`}>{error ? noticeText(error) : readError ? noticeText(readError) : state?.error_details?.length ? state.error_details.map(noticeText).reduce((first, second) => t('ui.common.feedbackPair', {first, second})) : state?.error_copy ? noticeText(state.error_copy) : state?.error}</p>}
      {state?.tunnel_diagnostic && <details className="text-xs text-muted"><summary className="cursor-pointer">{t('ui.mcpService.diagnostic')}</summary><pre className="mt-2 whitespace-pre-wrap break-all">{state.tunnel_diagnostic}</pre></details>}
      {notice && <p role="status" className="text-xs text-muted">{noticeText(notice)}</p>}
    </div>
    <McpCompanionPanel state={state} changing={changing} onChange={setState} onSavePending={pending=>{companionSave.current=pending}} onBusy={value=>{
      companionWorking.current=value
      if(value) {generation.current++; setPassword('')}
    }}/>
  </section>
}
