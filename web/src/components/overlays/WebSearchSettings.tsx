import {SecretInput} from '@/components/ui/SecretInput'
import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { Switch } from '@/components/ui/primitives'
import { useEffect, useRef, useState } from 'react'
import { t, useLocale } from '@/i18n'
import { request } from '@/lib/http'
import { Button } from '@/components/ui/primitives'

import type { UiSettings as Settings, UiSettingsResponse as Reply } from '@/lib/protocol.generated'

const providers = ['deepseek', 'brave'] as const
type Provider = typeof providers[number]
type Draft = Record<Provider, { enabled: boolean; key: string }>
const emptyDraft = (): Draft => ({ deepseek: { enabled: false, key: '' }, brave: { enabled: false, key: '' } })

export function WebSearchSettings() {
  useLocale()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [message, setMessage] = useState<ResourceText>('')
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    void request<Reply>('/api/v1/ui-settings', { signal: controller.signal }).then(result => {
      if (controller.signal.aborted) return
      if (result.ok) { setSettings(result.data.settings); setDraft(Object.fromEntries(providers.map(name => [name, { enabled: result.data.settings[`${name}_web_enabled`], key: '' }])) as Draft) }
      else setError(result.copy ?? result.message)
    })
    return () => { mounted.current = false; controller.abort() }
  }, [])
  function edit(next: Draft) {
    setDraft(next); setMessage('')
  }
  async function save() {
    if (!settings || busy) return
    setBusy(true); setError(''); setMessage('')
    const body = Object.fromEntries(providers.flatMap(name => [[`${name}_web_enabled`, draft[name].enabled], ...(draft[name].key.trim() ? [[`${name}_web_api_key`, draft[name].key.trim()]] : [])]))
    const result = await request<Reply>('/api/v1/ui-settings', { method: 'PATCH', body: JSON.stringify(body), timeout: 15000 })
    // 写入结果未知时只回查，不重放密钥提交。
    const check = await request<Reply>('/api/v1/ui-settings')
    if (!mounted.current) return
    setBusy(false)
    if (check.ok) setSettings(check.data.settings)
    if (result.ok && !result.data.rejected?.length && check.ok && providers.every(name => check.data.settings[`${name}_web_enabled`] === draft[name].enabled && (!draft[name].key.trim() || check.data.settings[`${name}_web_api_key_configured`]))) {
      setDraft(current => Object.fromEntries(providers.map(name => [name, { ...current[name], key: '' }])) as Draft); setMessage({key: 'ui.webSearch.saved'})
    } else setError(result.ok ? {key: 'ui.webSearch.unknown'} : result.copy ?? result.message)
  }
  return <section className="space-y-3 rounded-xl border border-line p-4" aria-label={t('ui.webSearch.title')}>
    <h3 className="text-sm font-semibold">{t('ui.webSearch.title')}</h3>
    <p className="text-xs leading-relaxed text-muted">{t('ui.webSearch.hint')}</p>
    <details className="text-xs text-muted"><summary className="cursor-pointer">{t('ui.webSearch.order')}</summary><p className="mt-2 leading-relaxed">{t('ui.webSearch.note')}</p></details>
    {error ? <p role="alert" className="text-xs text-danger">{noticeText(error)}</p> : null}
    {!settings ? !error && <p role="status" className="text-xs text-muted">{t('ui.webSearch.loading')}</p> : <>
      {providers.map(name => <fieldset key={name} className="space-y-3 rounded-lg border border-line p-3" disabled={busy}>
        <legend className="px-1 text-xs font-medium">{name === 'deepseek' ? 'DeepSeek' : 'Brave Search API'}</legend>
        <label className="flex items-center gap-2 text-xs"><Switch checked={draft[name].enabled} onChange={event => edit({ ...draft, [name]: { ...draft[name], enabled: event.target.checked } })} />{t(`ui.webSearch.${name}.enable`)}</label>
        <label className="block space-y-1 text-xs"><span>{t('ui.webSearch.apiKeyLabel', {provider: name === 'deepseek' ? 'DeepSeek' : 'Brave Search'})}</span><SecretInput autoComplete="new-password" className="ui-field w-full" value={draft[name].key} placeholder={settings[`${name}_web_api_key_mask`] ? t('ui.components.overlays.ModelConnections.94fcf2ddcd2b', { p0: settings[`${name}_web_api_key_mask`]! }) : t('ui.webSearch.enterKey')} onChange={event => edit({ ...draft, [name]: { ...draft[name], key: event.target.value } })} /></label>
      </fieldset>)}
      <p className="text-2xs text-muted">{t('ui.webSearch.keyNote')}</p>
      <div className="flex flex-wrap items-center gap-3"><Button size="sm" disabled={busy || providers.some(name => draft[name].enabled && !draft[name].key.trim() && !settings[`${name}_web_api_key_configured`])} onClick={() => void save()}>{t(busy ? 'ui.webSearch.saving' : 'ui.webSearch.save')}</Button><span role="status" className="text-xs text-muted">{noticeText(message)}</span></div>
    </>}
  </section>
}
