import {createPortal} from 'react-dom'
import {useEffect, useRef, useState, type RefObject} from 'react'
import {Loader2, Mic} from 'lucide-react'
import {Button, Tooltip} from '@/components/ui/primitives'
import {t, useLocale} from '@/i18n'
import {ApiError, unwrap} from '@/lib/http'
import {prepareVoiceInput, voiceRequest} from '@/lib/voiceApi'
import {noticeText} from '@/lib/noticeText'
import {failureText, ResourceError} from '@/lib/resourceError'
import {createVoiceRecorder, type VoiceRecorder} from '@/lib/voiceRecorder'
import {VoiceWaveform, updateVoiceWaveform} from './VoiceWaveform'
import {useAppStore} from '@/store/app'
import type {NoticeText} from '@/types'
import {desktopVoice} from '@/lib/desktopVoice'

type Phase = 'idle' | 'opening' | 'recording' | 'processing'
interface Operation {disposed: boolean; dispose(): void; recorder?: VoiceRecorder; preparation?: ReturnType<typeof prepareVoiceInput>}

export function VoiceButton({editor, disabled, onTranscript}: {
  editor: RefObject<HTMLTextAreaElement>; disabled: boolean; onTranscript(text: string): void
}) {
  useLocale()
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<NoticeText | null>(null)
  const [diagnostic, setDiagnostic] = useState<string | null>(null)
  const waveform = useRef<SVGSVGElement>(null)
  const operation = useRef<Operation | null>(null)
  const mounted = useRef(false)
  const callback = useRef(onTranscript)
  callback.current = onTranscript
  const disabledRef = useRef(disabled)
  const globalPublish = useRef(() => {})
  disabledRef.current = disabled
  const invalidate = () => {
    const current = operation.current
    operation.current = null
    if (current) {current.disposed = true; current.dispose()}
  }
  useEffect(() => {
    mounted.current = true
    const dispose = () => {invalidate(); if (mounted.current) {setPhase('idle'); setError(null); setDiagnostic(null)}}
    const unsubscribe = useAppStore.subscribe((next, previous) => {
      if (next.activeSessionId !== previous.activeSessionId || next.newSessionRevision !== previous.newSessionRevision) dispose()
    })
    const hidden = () => {if (document.hidden) dispose()}
    document.addEventListener('visibilitychange', hidden)
    return () => {mounted.current = false; unsubscribe(); document.removeEventListener('visibilitychange', hidden); invalidate()}
  }, [])
  useEffect(() => {if (disabled) {invalidate(); setPhase('idle')}; globalPublish.current()}, [disabled])
  useEffect(() => {
    const voice = desktopVoice()
    if (!voice) return
    const id = crypto.randomUUID()
    let live = true
    let focused = false
    let generation = 0, composing = false
    const seen = new Map<string, boolean>()
    const publish = () => {
      const current = document.activeElement === editor.current && !document.hidden && !disabledRef.current
      if (!current && !focused) return
      if (current !== focused) generation++
      focused = current
      const state = useAppStore.getState()
      void voice.context({id, sessionId:state.activeSessionId, revision:state.newSessionRevision, generation, composing, editor:current}).catch(failure => console.error('Voice context failed:', failure))
    }
    const unsubscribe = useAppStore.subscribe((next, previous) => {
      if (next.activeSessionId !== previous.activeSessionId || next.newSessionRevision !== previous.newSessionRevision) {generation++; publish()}
    })
    const insert = voice.onInsert(value => {
      if (!live || !value?.context || value.context.id !== id || typeof value.id !== 'string' || typeof value.transcript !== 'string' || value.transcript.length > 16000) return
      const state = useAppStore.getState()
      let accepted = seen.get(value.id)
      if (accepted === undefined) {
        accepted = !disabledRef.current && !document.hidden && document.activeElement === editor.current
          && !composing && generation === value.context.generation && state.activeSessionId === value.context.sessionId && state.newSessionRevision === value.context.revision
        if (accepted) callback.current(value.transcript)
        seen.set(value.id, accepted)
        if (seen.size > 64) seen.delete(seen.keys().next().value!)
      }
      void voice.receipt(value.id, accepted).catch(failure => console.error('Voice insertion receipt failed:', failure))
    })
    document.addEventListener('focusin', publish)
    document.addEventListener('focusout', publish)
    document.addEventListener('visibilitychange', publish)
    const compositionStart = () => {composing = true; generation++; publish()}
    const compositionEnd = () => {composing = false; generation++; publish()}
    document.addEventListener('compositionstart', compositionStart)
    document.addEventListener('compositionend', compositionEnd)
    globalPublish.current = publish
    publish()
    return () => {
      live = false; globalPublish.current = () => {}
      unsubscribe(); insert()
      document.removeEventListener('focusin', publish); document.removeEventListener('focusout', publish); document.removeEventListener('visibilitychange', publish)
      document.removeEventListener('compositionstart', compositionStart); document.removeEventListener('compositionend', compositionEnd)
      if (focused) void voice.context({id, sessionId:useAppStore.getState().activeSessionId, revision:useAppStore.getState().newSessionRevision, generation, composing, editor:false}).catch(failure => console.error('Voice context release failed:', failure))
    }
  }, [editor])

  const toggle = async () => {
    const existing = operation.current
    if (existing) {
      if (!existing.recorder || existing.disposed) return
      const recorder = existing.recorder
      existing.recorder = undefined
      setPhase('processing')
      try {
        const wav = await recorder.finish()
        if (existing.disposed) return
        if (wav.byteLength <= 44) {setError({key: 'ui.voice.empty'}); return}
        await unwrap(existing.preparation!)
        if (existing.disposed || !mounted.current) return
        const result = await unwrap(voiceRequest<{transcript: string}>('/api/v1/voice/transcriptions', {
          method: 'POST', headers: {'Content-Type': 'audio/wav'}, body: wav, timeout: 780000,
        }))
        if (existing.disposed || !mounted.current) return
        if (result.transcript.trim()) callback.current(result.transcript)
        else setError({key: 'ui.voice.empty'})
      } catch (failure) {
        if (!existing.disposed && mounted.current) {
          const copy = failure instanceof ApiError ? (failure.details as {copy?: NoticeText} | undefined)?.copy : undefined
          setError(copy ?? failureText(failure))
          const reason = failure instanceof ApiError ? (failure.details as {reason?: unknown} | undefined)?.reason : undefined
          setDiagnostic(typeof reason === 'string' ? reason : null)
        }
      } finally {
        if (operation.current === existing) {operation.current = null; if (mounted.current) setPhase('idle')}
        existing.dispose()
      }
      return
    }
    if (disabledRef.current || document.hidden) return
    if (!navigator.mediaDevices?.getUserMedia || typeof AudioContext === 'undefined' || typeof AudioWorkletNode === 'undefined') {
      setError({key: 'ui.voice.unsupported'}); return
    }
    setError(null)
    setDiagnostic(null)
    setPhase('opening')
    const current: Operation = {disposed: false, dispose: () => {}}
    operation.current = current
    const capture = createVoiceRecorder(() => {if (operation.current === current) void toggleRef.current()}, level => {
      if (mounted.current && !current.disposed && operation.current === current && current.recorder) {
        updateVoiceWaveform(waveform.current, level)
      }
    })
    current.dispose = capture.dispose
    try {
      const recorder = await capture.ready
      if (current.disposed || !mounted.current) {recorder.dispose(); return}
      current.recorder = recorder
      current.preparation = prepareVoiceInput()
      setPhase('recording')
    } catch (failure) {
      if (operation.current === current) {
        operation.current = null
        if (mounted.current) {setPhase('idle'); setError(failure instanceof ResourceError ? failureText(failure) : {key: 'ui.voice.micFailed'})}
      }
    }
  }
  const toggleRef = useRef(toggle)
  toggleRef.current = toggle
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.repeat || event.isComposing || event.keyCode === 229
          || !event.ctrlKey || !event.shiftKey || event.metaKey || event.altKey || event.code !== 'Space'
          || !editor.current?.closest('.composer-card')?.contains(document.activeElement)) return
      event.preventDefault()
      void toggleRef.current()
    }
    document.addEventListener('keydown', keydown)
    return () => document.removeEventListener('keydown', keydown)
  }, [editor])
  const rect = editor.current?.getBoundingClientRect()
  const feedbackStyle = rect ? {position: 'fixed' as const, left: Math.max(8, Math.min(rect.right - 256, window.innerWidth - 264)), bottom: Math.max(8, window.innerHeight - rect.top + 8)} : undefined
  const label = t(phase === 'opening' ? 'ui.voice.opening' : phase === 'recording' ? 'ui.voice.stop' : phase === 'processing' ? 'ui.voice.processing' : 'ui.voice.start')
  return <div className="relative flex items-center">
    <Tooltip content={phase === 'idle' ? <>{label}<br />{t('ui.voice.global')}</> : label}>
      <Button size="sm" variant={phase === 'recording' ? 'subtle' : 'ghost'} className="w-8 p-0" aria-label={label} title={label}
        aria-pressed={phase === 'recording'} disabled={disabled || phase === 'opening' || phase === 'processing'} onClick={() => toggle()}>
        {phase === 'opening' || phase === 'processing' ? <Loader2 className="h-4 w-4 animate-spin" />
          : phase === 'recording' ? <VoiceWaveform elementRef={waveform} /> : <Mic className="h-4.5 w-4.5" />}
      </Button>
    </Tooltip>
    {(phase !== 'idle' || error) && feedbackStyle && createPortal(<div role={error ? 'alert' : 'status'} style={feedbackStyle} className="z-50 w-64 max-w-screen rounded-xl border border-line bg-surface p-3 text-xs text-muted shadow-lg">
      {error ? noticeText(error) : t(phase === 'opening' ? 'ui.voice.opening' : phase === 'recording' ? 'ui.voice.recording' : phase === 'processing' ? 'ui.voice.processing' : 'ui.voice.start')}
      {error && diagnostic && <details className="mt-2"><summary>{t('ui.voice.diagnostic')}</summary><pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{diagnostic}</pre></details>}
    </div>, document.body)}
  </div>
}
