import {useEffect, useRef, useState} from 'react'
import {Mic} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {api} from '@/lib/api'
import {noticeText} from '@/lib/noticeText'
import {failureText} from '@/lib/resourceError'
import {desktopVoice, DEFAULT_VOICE_SHORTCUT, formatVoiceShortcut, recordedVoiceShortcut, type DesktopVoiceStatus} from '@/lib/desktopVoice'
import {usePageVisible} from '@/hooks/usePageVisible'
import {Button, RefreshButton} from '@/components/ui/primitives'
import type {ResourceText} from '@/types'
import type {VoiceDictationCapabilities} from '@/lib/protocol.generated'

export function VoiceInputPage() {
  useLocale()
  const visible = usePageVisible()
  const bridge = desktopVoice()
  const [engine, setEngine] = useState<VoiceDictationCapabilities | null>(null)
  const [status, setStatus] = useState<DesktopVoiceStatus | null>(null)
  const [enabled, setEnabled] = useState(false)
  const [shortcut, setShortcut] = useState(DEFAULT_VOICE_SHORTCUT)
  const [reading, setReading] = useState(false)
  const [writing, setWriting] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [notice, setNotice] = useState<ResourceText>('')
  const [diagnostic, setDiagnostic] = useState('')
  const generation = useRef(0)
  const edits = useRef(0)
  const working = useRef(false)
  const mounted = useRef(false)
  const reader = useRef<AbortController | null>(null)

  useEffect(() => {
    mounted.current = true
    return () => {mounted.current = false; generation.current++; reader.current?.abort()}
  }, [])

  async function refresh() {
    if (working.current || document.hidden) return
    const current = ++generation.current, edit = edits.current
    reader.current?.abort()
    const controller = new AbortController()
    reader.current = controller
    setReading(true)
    try {
      const [capabilities, desktop] = await Promise.all([api.voiceDictation(controller.signal), bridge?.status()])
      if (!mounted.current || current !== generation.current || controller.signal.aborted) return
      if (capabilities.ok) setEngine(capabilities.data)
      else {setEngine(null); setError((capabilities.details as {copy?: ResourceText} | undefined)?.copy ?? capabilities.copy ?? capabilities.message)}
      if (desktop?.ok) {
        setStatus(desktop.data)
        if (edits.current === edit) {setEnabled(desktop.data.enabled); setShortcut(desktop.data.shortcut)}
        if (capabilities.ok) setError(desktop.data.error ?? '')
      } else if (desktop && !desktop.ok) setError(desktop.copy)
      else if (capabilities.ok) setError('')
    } catch (failure) {
      if (mounted.current && current === generation.current && !controller.signal.aborted) setError(failureText(failure))
    } finally {if (mounted.current && current === generation.current) setReading(false)}
  }
  useEffect(() => {
    if (visible) void refresh()
    return () => {generation.current++; reader.current?.abort()}
  }, [visible])

  async function save() {
    if (!bridge || working.current || document.hidden || !engine?.available) return
    working.current = true
    reader.current?.abort()
    const current = ++generation.current, edit = edits.current
    setReading(false); setWriting(true); setError(''); setNotice(''); setDiagnostic('')
    try {
      const result = await bridge.configure({enabled, shortcut})
      if (!mounted.current || current !== generation.current || document.hidden) return
      if (result.ok) {
        setStatus(result.data)
        if (edits.current === edit) {setEnabled(result.data.enabled); setShortcut(result.data.shortcut)}
        setNotice({key:'ui.voice.global.saved'})
      } else {
        setError(result.copy)
        if (result.copy.key === 'ui.voice.global.saveUnconfirmed') {
          const checked = await bridge.status()
          if (mounted.current && current === generation.current && !document.hidden && checked.ok) setStatus(checked.data)
        }
      }
    } catch {
      if (mounted.current && current === generation.current && !document.hidden) {
        setError({key:'ui.voice.global.saveUnconfirmed'})
        // 保存可能已接受，只读回查，不重放开关或快捷键写入。
        try {
          const result = await bridge.status()
          if (mounted.current && current === generation.current && result.ok) setStatus(result.data)
        } catch (failure) {if (mounted.current && current === generation.current) setDiagnostic(failure instanceof Error ? failure.message : String(failure))}
      }
    } finally {working.current = false; if (mounted.current) {setWriting(false); if (current !== generation.current && !document.hidden) void refresh()}}
  }
  async function permission() {
    if (!bridge || working.current || document.hidden) return
    working.current = true
    const current = ++generation.current
    setWriting(true); setError('')
    try {
      const result = await bridge.permission()
      if (!mounted.current || current !== generation.current || document.hidden) return
      if (result.ok) setStatus(result.data)
      else setError(result.copy)
    } catch (failure) {if (mounted.current && current === generation.current) setError(failureText(failure))}
    finally {working.current = false; if (mounted.current) {setWriting(false); if (current !== generation.current && !document.hidden) void refresh()}}
  }

  return <section className="space-y-5">
    <h3 className="ui-section-title">{t('ui.voice.dictation.title')}</h3>
    <div className="space-y-3">
      <h4 className="ui-card-title">{t('ui.voice.dictation.inHarness')}</h4>
      <p className="text-xs leading-reading text-muted">{t('ui.voice.dictation.composer', {shortcut:formatVoiceShortcut('Control+Shift+Space')})}</p>
    </div>
    <div className="space-y-4 rounded-xl border border-line p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h4 className="ui-card-title">{t('ui.voice.global.title')}</h4>
        <RefreshButton size="sm" label={t('ui.voice.dictation.refresh')} spinning={reading} disabled={reading || writing} onClick={() => void refresh()} />
      </div>
      {!bridge ? <p className="text-xs leading-reading text-muted">{t('ui.voice.global.desktopRequired')}</p> : <>
        <p role="status" className="text-xs text-muted">{t(status?.enabled ? 'ui.voice.global.enabled' : 'ui.voice.global.disabled')}</p>
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={enabled} disabled={reading || writing || !status?.supported}
          onChange={event => {edits.current++; setEnabled(event.target.checked); setNotice('')}} />{t('ui.voice.global.enable')}</label>
        <label className="block space-y-2 text-xs">
          <span>{t('ui.voice.global.shortcut')}</span>
          <input className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm" value={formatVoiceShortcut(shortcut)} disabled={writing || reading} spellCheck={false}
            readOnly aria-describedby="voice-shortcut-help" onKeyDown={event => {
              if (event.nativeEvent.isComposing || event.keyCode === 229) return
              if (event.key === 'Tab' && !event.ctrlKey && !event.altKey && !event.metaKey) return
              if (event.key === 'Escape' && !event.ctrlKey && !event.altKey && !event.metaKey && !event.shiftKey) {event.stopPropagation(); event.currentTarget.blur(); return}
              event.preventDefault(); event.stopPropagation()
              const value = recordedVoiceShortcut(event.nativeEvent)
              if (value) {edits.current++; setShortcut(value); setNotice('')}
            }} />
        </label>
        <p id="voice-shortcut-help" className="text-xs leading-reading text-faint">{t('ui.voice.global.toggleHelp', {shortcut:formatVoiceShortcut(DEFAULT_VOICE_SHORTCUT)})}</p>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="primary" icon={<Mic aria-hidden className="h-4 w-4" />} loading={writing} disabled={reading || !engine?.available || !status?.supported} onClick={() => void save()}>{t('ui.voice.global.save')}</Button>
          {status && !status.accessibility && <Button size="sm" disabled={writing || reading} onClick={() => void permission()}>{t('ui.voice.global.permission')}</Button>}
        </div>
        {status && !status.accessibility && <p className="text-xs leading-reading text-warn">{t('ui.voice.global.permissionHelp')}</p>}
        <p className="text-xs leading-reading text-faint">{t('ui.voice.global.lifecycle')}</p>
      </>}
      {reading && <p role="status" className="text-xs text-muted">{t('ui.voice.dictation.checking')}</p>}
      {engine && !engine.available && engine.reason && <p role="status" className="text-xs text-warn">{noticeText(engine.reason)}</p>}
      {error && <p role="alert" className="break-words text-xs text-danger">{noticeText(error)}</p>}
      {diagnostic && <details className="text-xs text-muted"><summary>{t('ui.voice.diagnostic')}</summary><pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono">{diagnostic}</pre></details>}
      {notice && <p role="status" className="text-xs text-muted">{noticeText(notice)}</p>}
    </div>
  </section>
}
