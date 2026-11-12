import type { ResourceText } from '@/types'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { runtimeRequest } from '@/lib/http'
import { useAppStore } from '@/store/app'
import { permissionProfile, permissionProfiles } from '@/lib/permissions'
import { usePermissionStore } from '@/store/permissions'
import { Button } from '@/components/ui/primitives'
import {parseInteger} from '@/lib/settingsValidation'

const limits = {
  max_steps: 'ui.components.overlays.CapabilitiesPanel.a4e6d1de3d04',
  token_budget: 'ui.components.overlays.CapabilitiesPanel.07531aaa3d3c',
} as const
const requiredLimits = {network_timeout: 'ui.components.overlays.CapabilitiesPanel.064f6376e986'} as const

type Limit = keyof typeof limits
type RequiredLimit = keyof typeof requiredLimits
type Config = { permission_policy: string } & Record<Limit, number | null> & Record<RequiredLimit, number>

export function NumberField({label, value, busy, placeholder, onBlur, allowEmpty = false}: {
  label: string
  value: number | null
  busy: boolean
  placeholder?: string
  allowEmpty?: boolean
  onBlur: (raw: string) => Promise<number | null | undefined>
}) {
  const [raw, setRaw] = useState(String(value ?? ''))
  const dirty = useRef(false)
  const focused = useRef(false)
  const version = useRef(0)
  const alive = useRef(true)
  const composing = useRef(false)
  const [invalid, setInvalid] = useState(false)
  useLayoutEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  useEffect(() => {if (!dirty.current && !focused.current) setRaw(String(value ?? ''))}, [value])
  useLocale()
  return <label className="block space-y-2 text-xs font-medium text-ink"><span>{label}</span><input aria-label={label} aria-invalid={invalid} className="ui-field" disabled={busy} type="number" min="1" step="1" placeholder={placeholder} value={raw}
    onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
    onFocus={() => {focused.current = true}}
    onChange={event => {dirty.current = true; version.current++; setRaw(event.target.value); setInvalid(false)}}
    onBlur={async event => {
      focused.current = false
      if (!dirty.current) {setRaw(String(value ?? '')); return}
      if (composing.current) return
      if (!(allowEmpty && event.target.value === '') && parseInteger(event.target.value) === undefined) {setInvalid(true); return}
      setInvalid(false)
      const ticket = version.current
      const saved = await onBlur(event.target.value)
      if (alive.current && ticket === version.current && saved !== undefined) {dirty.current = false; setRaw(String(saved ?? ''))}
    }} />{invalid ? <span role="alert" className="block text-danger">{t(allowEmpty ? 'ui.settings.optionalPositiveInteger' : 'ui.settings.positiveInteger')}</span> : null}</label>
}

export function CapabilitiesPanel() {
  const locale = useLocale()
  const toast = useAppStore(state => state.toast)
  const [config, setConfig] = useState<Config | null>(null)
  const [actionError, setError] = useState<ResourceText>('')
  const [readError, setReadError] = useState<ResourceText>('')
  const error: ResourceText = actionError && readError ? {template: {key: 'ui.common.feedbackPair', params: {first: actionError, second: readError}}} : actionError || readError
  const [busy, setBusy] = useState(false)
  const mounted = useRef(false)
  const reading = useRef<AbortController>()
  const working = useRef(false)
  const readAgain = useRef(false)

  const load = async () => {
    if (!mounted.current) return
    reading.current?.abort()
    const controller = reading.current = new AbortController()
    setReadError('')
    try {
      const data = await runtimeRequest<{ config: Config }>('/settings', 'GET', undefined, {signal: controller.signal})
      if (controller.signal.aborted || !mounted.current) return
      if (!usePermissionStore.getState().sessionId) usePermissionStore.setState({policy: data.config.permission_policy})
      setConfig(data.config); setReadError('')
      return data.config
    } catch (failure) {
      if (!controller.signal.aborted && mounted.current) setReadError(failureText(failure))
    } finally {
      if (reading.current === controller) reading.current = undefined
    }
  }
  useLayoutEffect(() => {
    mounted.current = true
    return () => {mounted.current = false; reading.current?.abort()}
  }, [])
  useEffect(() => {
    if (working.current) readAgain.current = true
    else void load()
    return () => {reading.current?.abort()}
  }, [locale])
  const perform = async (operation: () => Promise<unknown>) => {
    if (!mounted.current || working.current) return
    working.current = true
    reading.current?.abort()
    setBusy(true); setError('')
    const readCurrent = async () => {
      let loaded: Config | undefined
      do {
        readAgain.current = false
        loaded = await load()
      } while (mounted.current && readAgain.current)
      return loaded
    }
    try {
      await operation()
      if (mounted.current) {
        const loaded = await readCurrent()
        if (loaded && mounted.current) toast({title: {key: 'ui.components.overlays.CapabilitiesPanel.99ddd128a7cd'}, tone: 'success'})
        return loaded
      }
    } catch (failure) {
      if (mounted.current) {
        const issue = failureText(failure)
        await readCurrent()
        if (mounted.current) setError(issue)
      }
    } finally {
      working.current = false
      if (mounted.current) setBusy(false)
    }
  }
  if (!config) return error ? <div role="alert" className="space-y-2 text-sm text-danger"><p>{noticeText(error)}</p><Button size="sm" onClick={() => void load()}>{t('ui.requestComposition.retry')}</Button></div>
    : <p role="status">{t('ui.components.overlays.CapabilitiesPanel.21bd738e0d71')}</p>
  return <div className="space-y-4">
    {error && <p role="alert" className="text-sm text-danger">{noticeText(error)}</p>}
    <h3 className="ui-section-title">{t('ui.components.overlays.CapabilitiesPanel.46b9b10596d1')}</h3>
    <label className="block space-y-2 text-xs font-medium text-ink"><span>{t('ui.components.overlays.CapabilitiesPanel.d275514a4a9d')}</span><select aria-label={t('ui.components.overlays.CapabilitiesPanel.d275514a4a9d')} disabled={busy} className="ui-field" value={permissionProfile(config.permission_policy) ?? config.permission_policy} onChange={event => void perform(() => runtimeRequest('/settings', 'PATCH', {permission_policy: event.target.value}))}>
      {config.permission_policy === 'read_only' && <option value="read_only" disabled hidden>{t('ui.components.chat.PermissionMenu.c0702d24132b')}</option>}
      {permissionProfiles.map(profile => <option key={profile.id} value={profile.id}>{profile.label}</option>)}
    </select></label>
    <p className="text-2xs leading-relaxed text-faint [overflow-wrap:anywhere]">{t('ui.components.overlays.CapabilitiesPanel.1a070c75f39e')}</p>
    <p className="text-2xs leading-relaxed text-faint [overflow-wrap:anywhere]">{t('ui.components.overlays.CapabilitiesPanel.bb1d1b5d684c')}</p>
    <p id="capabilities-limit-save-hint" className="text-2xs leading-relaxed text-faint">{t('ui.capabilities.limitSaveHint')}</p>
    <div role="group" aria-describedby="capabilities-limit-hint capabilities-limit-save-hint" className="space-y-4">
      <p id="capabilities-limit-hint" className="text-2xs leading-relaxed text-faint [overflow-wrap:anywhere]">{t('ui.components.overlays.CapabilitiesPanel.cdf5a33e8019')}</p>
      {(Object.keys(limits) as Limit[]).map(key => <NumberField key={key} label={t(limits[key])} value={config[key]} busy={busy} allowEmpty placeholder={t('ui.components.overlays.CapabilitiesPanel.8651f50cc425')} onBlur={async raw => {
        const value = raw === '' ? null : Number(raw)
        if (value === config[key]) return config[key]
        const saved = await perform(() => runtimeRequest('/settings', 'PATCH', {[key]: value}))
        return saved?.[key]
      }} />)}
    </div>
    {(Object.keys(requiredLimits) as RequiredLimit[]).map(key => <NumberField key={key} label={t(requiredLimits[key])} value={config[key]} busy={busy} onBlur={async raw => {
      const value = Number(raw)
      if (value === config[key]) return config[key]
      const saved = await perform(() => runtimeRequest('/settings', 'PATCH', {[key]: value}))
      return saved?.[key]
    }} />)}
  </div>
}
