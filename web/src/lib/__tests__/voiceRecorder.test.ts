import {afterEach, expect, it, vi} from 'vitest'
import {createVoiceRecorder, encodeVoiceWav} from '../voiceRecorder'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {setLocale, t} from '@/i18n'

afterEach(async () => {vi.unstubAllGlobals(); await setLocale('en')})
it('encodes signed PCM16 mono audio with a truthful 16 kHz WAV header', () => {
  const audio = encodeVoiceWav(new Float32Array([-2, -1, 0, 1, 2]))
  const data = new DataView(audio)
  expect(new TextDecoder().decode(audio.slice(0, 4))).toBe('RIFF')
  expect(new TextDecoder().decode(audio.slice(8, 12))).toBe('WAVE')
  expect(data.getUint32(4, true)).toBe(audio.byteLength - 8)
  expect(data.getUint16(22, true)).toBe(1)
  expect(data.getUint32(24, true)).toBe(16000)
  expect(data.getUint32(40, true)).toBe(10)
  expect([0, 1, 2, 3, 4].map(index => data.getInt16(44 + index * 2, true))).toEqual([-32768, -32768, 0, 32767, 32767])
})

it('stops tracks when microphone permission arrives after the owner was disposed', async () => {
  let grant!: (value: unknown) => void
  const permission = new Promise(resolve => {grant = resolve})
  const stop = vi.fn()
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia: vi.fn(() => permission)}})
  const capture = createVoiceRecorder(vi.fn())
  capture.dispose()
  grant({getTracks: () => [{stop}]})
  await expect(capture.ready).rejects.toMatchObject({name: 'AbortError'})
  expect(stop).toHaveBeenCalledOnce()
})

it('feeds the real captured audio into the level meter without changing its samples', async () => {
  const stop = vi.fn(), onLevel = vi.fn()
  const port = {onmessage: null as ((event: MessageEvent<Float32Array>) => void) | null, close: vi.fn()}
  const mute = {gain: {value: 1}, connect: vi.fn()}
  const close = vi.fn(async () => {})
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia: async () => ({getTracks: () => [{stop}]})}})
  vi.stubGlobal('AudioContext', class {
    sampleRate = 16000; state = 'running'; destination = {}; audioWorklet = {addModule: async () => {}}
    createMediaStreamSource() {return {connect: vi.fn(), disconnect: vi.fn()}}
    createGain() {return mute}
    resume = async () => {}
    close = close
  })
  vi.stubGlobal('AudioWorkletNode', class {port = port; connect = () => mute; disconnect = vi.fn()})
  const capture = createVoiceRecorder(vi.fn(), onLevel)
  const recorder = await capture.ready
  try {
    const samples = Float32Array.from({length: 1600}, (_, index) => Math.sin(index * Math.PI / 20) * .2)
    const original = samples.slice()
    port.onmessage!({data: samples} as MessageEvent<Float32Array>)
    expect(onLevel).toHaveBeenCalledTimes(2)
    expect(onLevel.mock.calls[0][0]).toBeGreaterThan(0)
    expect(samples).toEqual(original)
  } finally {recorder.dispose()}
  expect(port.onmessage).toBeNull()
  expect(stop).toHaveBeenCalledOnce()
  expect(close).toHaveBeenCalledOnce()
})

it('rechecks native microphone access on every attempt and keeps denial localized', async () => {
  const permission = vi.fn(async () => ({ok:true, data:{granted:false, copy:{key:'ui.systemPermission.microphone.settings'}}}))
  const getUserMedia = vi.fn()
  vi.stubGlobal('window', {harnessDesktop:{requestSystemPermission:permission}})
  vi.stubGlobal('navigator', {mediaDevices:{getUserMedia}})
  let failure:unknown
  try {await createVoiceRecorder(vi.fn()).ready} catch (error) {failure = error}
  expect(failureText(failure)).toEqual({key:'ui.systemPermission.microphone.settings'})
  for (const locale of ['en','zh-CN','en'] as const) {
    await setLocale(locale)
    expect(noticeText(failureText(failure))).toBe(t('ui.systemPermission.microphone.settings'))
  }
  expect(permission).toHaveBeenCalledOnce()
  await expect(createVoiceRecorder(vi.fn()).ready).rejects.toBeInstanceOf(Error)
  expect(permission).toHaveBeenCalledTimes(2)
  expect(getUserMedia).not.toHaveBeenCalled()
})

it('never opens a device if a system grant arrives after disposal', async () => {
  let resolve!:(value:unknown) => void
  const getUserMedia = vi.fn()
  vi.stubGlobal('window', {harnessDesktop:{requestSystemPermission:() => new Promise(done => {resolve = done})}})
  vi.stubGlobal('navigator', {mediaDevices:{getUserMedia}})
  const recording = createVoiceRecorder(vi.fn())
  recording.dispose()
  resolve({ok:true, data:{granted:true}})
  await expect(recording.ready).rejects.toMatchObject({name:'AbortError'})
  expect(getUserMedia).not.toHaveBeenCalled()
})

it('allows a browser microphone retry after denial and supplies actionable translated guidance', async () => {
  const getUserMedia = vi.fn(async () => {throw new DOMException('browser denied', 'NotAllowedError')})
  vi.stubGlobal('navigator', {mediaDevices:{getUserMedia}})
  for (let attempt = 0; attempt < 2; attempt++) {
    let failure:unknown
    try {await createVoiceRecorder(vi.fn()).ready} catch (error) {failure = error}
    expect(failureText(failure)).toEqual({key:'ui.systemPermission.microphone.browser'})
  }
  expect(getUserMedia).toHaveBeenCalledTimes(2)
})
