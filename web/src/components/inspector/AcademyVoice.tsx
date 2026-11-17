import {useEffect, useRef, useState} from 'react'
import {Loader2, Mic} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {Button} from '@/components/ui/primitives'
import {ApiError, unwrap} from '@/lib/http'
import {prepareVoiceInput, voiceRequest} from '@/lib/voiceApi'
import {createVoiceRecorder, type VoiceRecorder} from '@/lib/voiceRecorder'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import {useVoiceInputSupported} from '@/hooks/useVoiceInputSupported'
import type {NoticeText} from '@/types'

type Phase = 'idle' | 'opening' | 'recording' | 'processing'
type Operation = {disposed: boolean; dispose(): void; recorder?: VoiceRecorder; preparation?: ReturnType<typeof prepareVoiceInput>}

/** 学习区语音输入：复用 hex-stt 录音/转写；不支持或隐藏时不挂载录音，转写文本追加到目标输入。 */
export function AcademyVoiceButton({active, disabled, onText}: {active: boolean; disabled?: boolean; onText: (text: string) => void}) {
  useLocale()
  const supported = useVoiceInputSupported(active)
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<NoticeText | null>(null)
  const operation = useRef<Operation | null>(null)
  const mounted = useRef(true)
  const callback = useRef(onText)
  callback.current = onText
  useEffect(() => () => {mounted.current = false; operation.current?.dispose(); operation.current = null}, [])
  if (!supported) return null
  const toggle = async () => {
    const existing = operation.current
    if (existing) {
      if (!existing.recorder || existing.disposed) return
      const recorder = existing.recorder
      existing.recorder = undefined
      setPhase('processing')
      try {
        const wav = await recorder.finish()
        if (existing.disposed || !mounted.current) return
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
        }
      } finally {
        if (operation.current === existing) {operation.current = null; if (mounted.current) setPhase('idle')}
        existing.dispose()
      }
      return
    }
    if (disabled || document.hidden) return
    if (!navigator.mediaDevices?.getUserMedia || typeof AudioContext === 'undefined' || typeof AudioWorkletNode === 'undefined') {
      setError({key: 'ui.voice.unsupported'}); return
    }
    setError(null)
    setPhase('opening')
    const current: Operation = {disposed: false, dispose: () => {}}
    operation.current = current
    const capture = createVoiceRecorder(() => {if (operation.current === current) void toggle()})
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
        if (mounted.current) {setPhase('idle'); setError(failureText(failure))}
      }
    }
  }
  const label = t(phase === 'opening' ? 'ui.voice.opening' : phase === 'recording' ? 'ui.voice.stop' : phase === 'processing' ? 'ui.voice.processing' : 'ui.voice.start')
  return <span className="inline-flex flex-col items-end gap-1">
    <Button size="xs" variant={phase === 'recording' ? 'subtle' : 'ghost'} data-academy-voice aria-label={label} title={label}
      aria-pressed={phase === 'recording'} disabled={disabled || phase === 'opening' || phase === 'processing'} onClick={() => void toggle()}
      icon={phase === 'opening' || phase === 'processing' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Mic className="h-3.5 w-3.5" />} />
    {error ? <span role="alert" className="text-xs text-danger">{noticeText(error)}</span> : null}
  </span>
}
