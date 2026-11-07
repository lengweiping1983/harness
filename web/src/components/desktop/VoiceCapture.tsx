import {useEffect, useRef, useState} from 'react'
import {Mic, Square, X} from 'lucide-react'
import {getLocale, setLocale, t, useLocale} from '@/i18n'
import {Button} from '@/components/ui/primitives'
import {createVoiceRecorder, type VoiceRecorder} from '@/lib/voiceRecorder'
import {desktopVoice, type VoiceAction} from '@/lib/desktopVoice'
import {unwrap, ApiError} from '@/lib/http'
import {prepareVoiceInput, voiceRequest} from '@/lib/voiceApi'
import {noticeText} from '@/lib/noticeText'
import {failureText, ResourceError} from '@/lib/resourceError'
import type {NoticeText} from '@/types'
import {VoiceWaveform, updateVoiceWaveform} from '@/components/chat/VoiceWaveform'

interface Capture {id: string; disposed: boolean; recorder?: VoiceRecorder; preparation?: ReturnType<typeof prepareVoiceInput>; dispose(): void}

export function VoiceCapture() {
  useLocale()
  const [phase, setPhase] = useState('opening')
  const [error, setError] = useState<NoticeText | null>(null)
  const [diagnostic, setDiagnostic] = useState('')
  const [result, setResult] = useState<'draft' | 'sent' | 'copied' | null>(null)
  const active = useRef<Capture | null>(null)
  const waveform = useRef<SVGSVGElement>(null)
  const action = useRef<(value: VoiceAction) => void>(() => {})
  const bridge = desktopVoice()
  const cancel = () => {
    const capture = active.current
    active.current = null
    if (capture) {capture.disposed = true; capture.dispose()}
    void bridge?.cancel(capture?.id)
  }
  const stop = async () => {
    const capture = active.current
    if (!bridge || !capture?.recorder || capture.disposed) return
    const recorder = capture.recorder
    capture.recorder = undefined
    setPhase('processing')
    try {
      const phase = await bridge.phase(capture.id, 'processing')
      if (!phase.ok) throw new ResourceError(phase.copy)
      const wav = await recorder.finish()
      if (capture.disposed) return
      if (wav.byteLength <= 44) throw new ResourceError({key:'ui.voice.empty'})
      await unwrap(capture.preparation!)
      if (capture.disposed || active.current !== capture) return
      const value = await unwrap(voiceRequest<{transcript: string}>('/api/v1/voice/transcriptions', {
        method:'POST', headers:{'Content-Type':'audio/wav'}, body:wav, timeout:780000,
      }))
      if (capture.disposed || active.current !== capture) return
      if (!value.transcript.trim()) throw new ResourceError({key:'ui.voice.empty'})
      const inserted = await bridge.complete(capture.id, value.transcript)
      if (capture.disposed || active.current !== capture) return
      if (!inserted.ok) {setError(inserted.copy); setDiagnostic(inserted.diagnostic ?? '')}
      else setResult(inserted.data.result)
      setPhase('done')
    } catch (failure) {
      if (!capture.disposed && active.current === capture) {
        const copy = failure instanceof ApiError ? (failure.details as {copy?: NoticeText} | undefined)?.copy : undefined
        setError(copy ?? failureText(failure))
        const reason = failure instanceof ApiError ? (failure.details as {reason?: unknown} | undefined)?.reason : undefined
        setDiagnostic(typeof reason === 'string' ? reason : '')
        setPhase('done')
      }
    } finally {capture.dispose(); if (!capture.disposed && active.current === capture) void bridge.phase(capture.id, 'done')}
  }
  action.current = value => {
    if (value.action === 'error') {
      const previous = active.current; active.current = null
      if (previous) {previous.disposed = true; previous.dispose()}
      setError(value.copy ?? {key:'ui.voice.global.failed'}); setDiagnostic(value.diagnostic ?? ''); setPhase('done')
      return
    }
    if (value.action === 'cancel') {
      const previous = active.current; active.current = null
      if (previous) {previous.disposed = true; previous.dispose()}
      return
    }
    if (value.action === 'stop') {if (active.current?.id === value.id) void stop(); return}
    if (!value.id || active.current?.id === value.id) return
    const previous = active.current
    if (previous) {previous.disposed = true; previous.dispose()}
    setError(null); setDiagnostic(''); setResult(null); setPhase('opening')
    const current: Capture = {id:value.id, disposed:false, dispose:() => {}}
    active.current = current
    const recording = createVoiceRecorder(() => void stopRef.current(), level => {
      if (!current.disposed && active.current === current && current.recorder) updateVoiceWaveform(waveform.current, level)
    })
    current.dispose = recording.dispose
    void recording.ready.then(async recorder => {
      if (current.disposed || active.current !== current) {recorder.dispose(); return}
      current.recorder = recorder
      current.preparation = prepareVoiceInput()
      const reply = await bridge!.phase(current.id, 'recording')
      if (current.disposed || active.current !== current) return
      if (!reply.ok) {current.dispose(); setError(reply.copy); setPhase('done'); return}
      if (current.recorder === recorder) setPhase('recording')
    }).catch(failure => {
      current.dispose()
      if (!current.disposed && active.current === current) {setError(failure instanceof ResourceError ? failureText(failure) : {key:'ui.voice.micFailed'}); setPhase('done'); void bridge!.phase(current.id, 'done')}
    })
  }
  const stopRef = useRef(stop)
  stopRef.current = stop
  useEffect(() => {
    const language = (event: StorageEvent) => {
      if (event.key === 'harness.language.effective.v1' && (event.newValue === 'en' || event.newValue === 'zh-CN') && event.newValue !== getLocale()) void setLocale(event.newValue)
      if (event.key === 'harness.ui-theme') document.documentElement.classList.toggle('dark', event.newValue === 'dark' || event.newValue !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches)
    }
    window.addEventListener('storage', language)
    return () => window.removeEventListener('storage', language)
  }, [])
  useEffect(() => {
    if (!bridge) {setError({key:'ui.voice.global.desktopRequired'}); return}
    let live = true
    const unsubscribe = bridge.onAction(value => action.current(value))
    void bridge.ready().then(reply => {if (live) {if (reply.ok && reply.data) action.current(reply.data); else if (!reply.ok) setError(reply.copy)}}).catch(failure => {if (live) {setError({key:'ui.voice.global.failed'}); setDiagnostic(String(failure))}})
    return () => {live = false; unsubscribe(); const current = active.current; active.current = null; if (current) {current.disposed = true; current.dispose()}}
  }, [])
  return <main className="flex min-h-dvh flex-col justify-center gap-3 bg-surface p-4 text-ink">
    <div className="flex items-center gap-3">
      {phase === 'recording' ? <VoiceWaveform elementRef={waveform} /> : <Mic aria-hidden className="h-5 w-5 text-brand" />}
      <h1 className="ui-card-title flex-1">{t('ui.voice.global.title')}</h1>
      <Button size="sm" variant="ghost" aria-label={t('ui.voice.global.close')} onClick={cancel}><X aria-hidden className="h-4 w-4" /></Button>
    </div>
    <p role={error ? 'alert' : 'status'} className={error ? 'text-xs text-warn' : 'text-xs text-muted'}>
      {error ? noticeText(error) : result ? t(result === 'draft' ? 'ui.voice.global.draft' : result === 'sent' ? 'ui.voice.global.sent' : 'ui.voice.global.copied') : t(phase === 'recording' ? 'ui.voice.recording' : phase === 'processing' ? 'ui.voice.processing' : 'ui.voice.opening')}
    </p>
    {diagnostic && <details className="text-xs text-muted"><summary>{t('ui.voice.diagnostic')}</summary><pre className="max-h-16 overflow-auto whitespace-pre-wrap break-words font-mono">{diagnostic}</pre></details>}
    {phase === 'recording' && <Button size="sm" icon={<Square aria-hidden className="h-3 w-3" />} onClick={() => void stop()}>{t('ui.voice.stop')}</Button>}
  </main>
}
