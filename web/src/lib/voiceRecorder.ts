import {createVoiceLevelMeter} from './voiceLevel'
import {authorizeMicrophone} from './systemPermissions'
import {ResourceError} from './resourceError'

export const MAX_RECORDING_SECONDS = 60
const TARGET_RATE = 16000

/** Hex accepts PCM WAV; microphone audio never leaves this browser until finish. */
export function encodeVoiceWav(samples: Float32Array): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const text = (offset: number, value: string) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)))
  text(0, 'RIFF'); view.setUint32(4, buffer.byteLength - 8, true); text(8, 'WAVE')
  text(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true)
  view.setUint16(22, 1, true); view.setUint32(24, TARGET_RATE, true)
  view.setUint32(28, TARGET_RATE * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true)
  text(36, 'data'); view.setUint32(40, samples.length * 2, true)
  samples.forEach((sample, index) => {
    const value = Math.max(-1, Math.min(1, sample))
    view.setInt16(44 + index * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true)
  })
  return buffer
}

export interface VoiceRecorder {
  finish(): Promise<ArrayBuffer>
  dispose(): void
}

/** Every resource is owned before awaiting; a late permission grant still closes its tracks. */
export function createVoiceRecorder(onLimit: () => void, onLevel?: (level: number) => void): {ready: Promise<VoiceRecorder>; dispose(): void} {
  let disposed = false
  let stream: MediaStream | undefined
  let context: AudioContext | undefined
  let node: AudioWorkletNode | undefined
  let source: MediaStreamAudioSourceNode | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  let chunks: Float32Array[] = []
  let frames = 0
  let closing: Promise<void> | undefined
  const dispose = () => {
    disposed = true
    clearTimeout(timer)
    if (node) {node.port.onmessage = null; node.disconnect(); node.port.close()}
    source?.disconnect()
    stream?.getTracks().forEach(track => track.stop())
    if (context && context.state !== 'closed' && !closing) closing = context.close()
    // close() can reject after the native device has already gone away; consumers await it on finish.
    void closing?.catch(() => {})
    chunks = []
  }
  const check = () => {if (disposed) throw new DOMException('Aborted', 'AbortError')}
  const ready = (async (): Promise<VoiceRecorder> => {
    try {
      if (typeof window !== 'undefined' && (window as unknown as {harnessDesktop?: {requestSystemPermission?: unknown}}).harnessDesktop?.requestSystemPermission) {
        await authorizeMicrophone()
        check()
      }
      stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}})
      check()
      context = new AudioContext()
      await context.audioWorklet.addModule(new URL('./voiceCapture.worklet.js', import.meta.url).href)
      check()
      source = context.createMediaStreamSource(stream)
      node = new AudioWorkletNode(context, 'harness-voice-capture')
      const sampleRate = context.sampleRate
      const maxFrames = Math.floor(sampleRate * MAX_RECORDING_SECONDS)
      const meter = onLevel ? createVoiceLevelMeter(sampleRate, onLevel) : undefined
      node.port.onmessage = (event: MessageEvent<Float32Array>) => {
        if (disposed || !(event.data instanceof Float32Array)) return
        const chunk = event.data.slice(0, maxFrames - frames)
        if (chunk.length) {chunks.push(chunk); frames += chunk.length; meter?.(chunk)}
        if (frames >= maxFrames) onLimit()
      }
      source.connect(node)
      const mute = context.createGain()
      mute.gain.value = 0
      node.connect(mute).connect(context.destination)
      await context.resume()
      check()
      timer = setTimeout(onLimit, MAX_RECORDING_SECONDS * 1000)
      return {
        dispose,
        async finish() {
          check()
          const audio = chunks
          const length = frames
          dispose()
          await closing
          if (!length) return encodeVoiceWav(new Float32Array())
          const offline = new OfflineAudioContext(1, Math.max(1, Math.floor(length * TARGET_RATE / sampleRate)), TARGET_RATE)
          const buffer = offline.createBuffer(1, length, sampleRate)
          let offset = 0
          for (const chunk of audio) {buffer.copyToChannel(new Float32Array(chunk), 0, offset); offset += chunk.length}
          const input = offline.createBufferSource()
          input.buffer = buffer
          input.connect(offline.destination)
          input.start()
          const rendered = await offline.startRendering()
          return encodeVoiceWav(rendered.getChannelData(0))
        },
      }
    } catch (error) {
      dispose()
      if (error instanceof DOMException && error.name === 'NotAllowedError') throw new ResourceError({key:'ui.systemPermission.microphone.browser'})
      throw error
    }
  })()
  return {ready, dispose}
}
