import {useEffect, useRef, useState} from 'react'
import {Copy} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {noticeText} from '@/lib/noticeText'
import {mcpCompanionConfigure, mcpCompanionToken, mcpServiceStatus, type McpServiceState} from '@/lib/api'
import {isUnconfirmedWrite} from '@/lib/http'
import {usePageVisible} from '@/hooks/usePageVisible'
import {Button} from '@/components/ui/primitives'
import {SecretInput} from '@/components/ui/SecretInput'
import type {ResourceText} from '@/types'
import {parseInteger} from '@/lib/settingsValidation'

export function McpCompanionPanel({state, changing, onChange, onBusy, onSavePending}: {state: McpServiceState | null; changing: boolean; onChange: (state: McpServiceState) => void; onBusy: (busy: boolean) => void; onSavePending?: (pending: Promise<boolean>) => void}) {
  useLocale()
  const companion = state?.companion
  const visible = usePageVisible()
  const enabled = companion?.enabled ?? false
  const [port, setPort] = useState('8877')
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [notice, setNotice] = useState<ResourceText>('')
  const edited = useRef(false)
  const generation = useRef(0)
  const mounted = useRef(true)
  const inFlight = useRef(false)
  const composing = useRef(false)
  const editVersion = useRef(0)
  const attemptedVersion = useRef(-1)
  useEffect(() => { mounted.current = true; return () => {mounted.current = false; generation.current++} }, [])
  useEffect(() => {
    if (companion && !edited.current) setPort(String(companion.port))
  }, [companion])
  useEffect(() => {generation.current++; setSecret('')}, [visible, companion?.status, companion?.endpoint])
  async function copy(value: string) {
    try {await navigator.clipboard.writeText(value); if(mounted.current) setNotice({key:'ui.mcpService.copied'})}
    catch {if(mounted.current) setError({key:'ui.mcpService.copyFailed'})}
  }
  function save(nextEnabled = enabled, applyPort = true): Promise<boolean> {
    if(inFlight.current || changing || !visible || !companion || composing.current) return Promise.resolve(false)
    const submittedPort = applyPort ? Number(port) : companion.port
    if (applyPort && (!/^\d+$/.test(port) || parseInteger(port, 1024, 65535) === undefined)) {
      setError({key: 'ui.companion.invalidPort'})
      return Promise.resolve(false)
    }
    if (applyPort && submittedPort === companion.port) {edited.current = false; return Promise.resolve(true)}
    if (applyPort && attemptedVersion.current === editVersion.current) return Promise.resolve(false)
    const submittedVersion = editVersion.current
    if (applyPort) attemptedVersion.current = submittedVersion
    inFlight.current = true
    const current = ++generation.current
    setBusy(true); setError(''); setNotice(''); setSecret('')
    onBusy(true)
    const pending = (async () => {
      try {
        const result = await mcpCompanionConfigure({enabled:nextEnabled, port:submittedPort})
        const confirmed = result.ok && result.data.companion?.port === submittedPort && result.data.companion.enabled === nextEnabled
        if ((result.ok && !confirmed) || (!result.ok && isUnconfirmedWrite(result))) {
          const check = await mcpServiceStatus()
          if (mounted.current && current === generation.current) {
            if (check.ok) onChange(check.data)
            setError({key: 'ui.companion.saveUnconfirmed'})
          }
          return false
        }
        if(!mounted.current || current !== generation.current) return confirmed
        if(result.ok && confirmed) {
          if(applyPort && submittedVersion === editVersion.current) {edited.current = false; setPort(String(submittedPort)); setNotice({key:'ui.companion.portSaved'})}
          onChange(result.data)
        } else if (!result.ok) setError(result.copy ?? result.message)
        return confirmed
      } finally {inFlight.current = false; onBusy(false); if(mounted.current) setBusy(false)}
    })()
    onSavePending?.(pending)
    return pending
  }
  async function reveal(show: boolean) {
    if(show && secret) {setSecret(''); generation.current++; return}
    if(inFlight.current || changing || !visible) return
    inFlight.current = true
    const current = generation.current
    setBusy(true); setError('')
    try {
      const result = await mcpCompanionToken()
      if(!mounted.current || current !== generation.current) return
      if(!result.ok) {setError(result.copy ?? result.message); return}
      if(show) setSecret(result.data.token)
      else await copy(result.data.token)
    } finally {inFlight.current = false; if(mounted.current) setBusy(false)}
  }
  const statuses = {running:'ui.companion.running', stopped:'ui.companion.stopped', disabled:'ui.companion.disabled', failed:'ui.companion.failed'} as const
  return <section className="rounded-xl border border-line bg-surface p-4 space-y-4">
    <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 flex-1"><h4 className="text-sm font-semibold text-ink">{t('ui.companion.title')}</h4><p className="mt-2 text-xs leading-relaxed text-muted">{t('ui.companion.description')}</p></div><Button size="sm" variant={enabled?'secondary':'primary'} disabled={!companion || busy || changing} onClick={()=>void save(!enabled, false)}>{t(enabled?'ui.companion.disable':'ui.companion.enable')}</Button></div>
    <fieldset disabled={!state || busy || changing} className="space-y-3 min-w-0">
      <label className="block text-sm text-ink">{t('ui.companion.port')}<input type="number" min="1024" max="65535" value={port} onChange={event=>{edited.current=true; editVersion.current++; setError(''); setNotice(''); setPort(event.target.value)}} onBlur={()=>{if(edited.current) void save()}} onCompositionStart={()=>{composing.current=true}} onCompositionEnd={()=>{composing.current=false}} className="mt-2 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink" /></label>
      <p className="text-xs text-muted">{t('ui.companion.portAutoSave')}</p>
    </fieldset>
    <p role="status" className="text-xs text-muted">{t(companion ? statuses[companion.status] : 'ui.mcpService.loading')}</p>
    {companion && <>
      <label className="block text-sm text-ink">{t('ui.companion.address')}<div className="mt-2 flex gap-2"><input readOnly aria-label={t('ui.companion.address')} value={companion.endpoint} className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"/><Button size="sm" variant="secondary" onClick={()=>void copy(companion.endpoint)}><Copy size={14}/>{t('ui.mcpService.copy')}</Button></div></label>
      <label className="block text-sm text-ink">{t('ui.companion.token')}<div className="mt-2 flex gap-2"><SecretInput containerClassName="flex-1" readOnly aria-label={t('ui.companion.token')} aria-description={t('ui.companion.tokenHint')} placeholder={t(enabled ? 'ui.companion.tokenPending' : 'ui.companion.tokenDisabled')} autoComplete="off" value={secret || companion.token_mask} className="bg-surface text-sm" concealedType="text" revealed={!!secret} toggleDisabled={busy || companion.status!=='running'} toggleBusy={busy} onToggleReveal={()=>void reveal(true)} /><Button size="sm" variant="secondary" disabled={busy || companion.status!=='running'} onClick={()=>void reveal(false)}><Copy size={14}/>{t('ui.mcpService.copy')}</Button></div></label>
      <p className="text-xs text-muted">{t('ui.companion.tokenHint')}</p>
      <label className="block text-sm text-ink">{companion.extension_version.trim() ? t('ui.companion.extension', {version:companion.extension_version}) : t('ui.companion.extensionLabel')}<div className="mt-2 flex gap-2"><input readOnly value={companion.extension_path} className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"/><Button size="sm" variant="secondary" onClick={()=>void copy(companion.extension_path)}><Copy size={14}/>{t('ui.mcpService.copy')}</Button></div></label>
      <p className="text-xs leading-relaxed text-muted">{t('ui.companion.install')}</p>
      {companion.error && <p role="alert" className="text-sm text-danger">{companion.error_copy ? noticeText(companion.error_copy) : companion.error}</p>}
    </>}
    {error && <p role="alert" className="text-sm text-danger">{noticeText(error)}</p>}
    {notice && <p role="status" className="text-xs text-muted">{noticeText(notice)}</p>}
  </section>
}
