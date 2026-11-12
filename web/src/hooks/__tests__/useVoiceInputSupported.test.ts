import {afterEach, beforeEach, expect, it, vi, type MockInstance} from 'vitest'
import {useVoiceInputSupported} from '../useVoiceInputSupported'
import {api} from '@/lib/api'

const hooks = vi.hoisted(() => ({cursor:0, slots:[] as unknown[], cleanups:new Map<number, () => void>(), visible:true}))
vi.mock('react', async original => ({...await original<typeof import('react')>(),
  useState:(initial:unknown) => {
    const index=hooks.cursor++
    if (!(index in hooks.slots)) hooks.slots[index]=initial
    return [hooks.slots[index], (value:unknown) => {hooks.slots[index]=value}]
  },
  useEffect:(effect:() => void | (() => void), deps:unknown[]) => {
    const index=hooks.cursor++, previous=hooks.slots[index] as unknown[] | undefined
    if (!previous || deps.some((value,i) => value !== previous[i])) {
      hooks.cleanups.get(index)?.(); hooks.cleanups.delete(index); hooks.slots[index]=deps
      const cleanup=effect(); if (cleanup) hooks.cleanups.set(index,cleanup)
    }
  },
}))
vi.mock('../usePageVisible', () => ({usePageVisible:() => hooks.visible}))
const fetchMock=vi.fn<typeof fetch>()
let inspection:MockInstance<typeof api.voiceDictation>
const capabilities=(cpu_supported:boolean, available=cpu_supported) => ({cpu_supported, available,
  platform:'windows', download_url:null, reason:cpu_supported ? null : {key:'ui.voice.global.cpuUnsupported'}})
const render=(active=true) => {hooks.cursor=0; return useVoiceInputSupported(active)}
const unmount=() => {for (const cleanup of hooks.cleanups.values()) cleanup(); hooks.cleanups.clear()}
beforeEach(() => {
  hooks.slots=[]; hooks.cleanups.clear(); hooks.visible=true; fetchMock.mockReset()
  vi.stubGlobal('fetch',fetchMock)
  inspection=vi.spyOn(api,'voiceDictation')
})
afterEach(() => {unmount(); vi.restoreAllMocks(); vi.unstubAllGlobals()})

it.each([true,false])('uses the validated server CPU capability %s before exposing voice input',async supported => {
  fetchMock.mockResolvedValue(Response.json(capabilities(supported)))
  expect(render()).toBe(false)
  await inspection.mock.results[0].value
  expect(render()).toBe(supported)
  expect(fetchMock).toHaveBeenCalledOnce()
  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/v1/voice/dictation')
})

it('retains supported CPU visibility when the optional source engine is missing',async () => {
  fetchMock.mockResolvedValue(Response.json({...capabilities(true,false),reason:{key:'ui.voice.dictation.missing'}}))
  render()
  await inspection.mock.results[0].value
  expect(render()).toBe(true)
})

it.each([{}, {cpu_supported:'true'}, {cpu_supported:true}])('does not expose voice for a malformed capability %j',async data => {
  fetchMock.mockResolvedValue(Response.json(data))
  render()
  expect(await inspection.mock.results[0].value).toMatchObject({ok:false,code:'response/invalid-schema'})
  expect(render()).toBe(false)
})

it('cancels closed or hidden observations and rejects late success before reading on return',async () => {
  let resolve!:(response:Response) => void
  fetchMock.mockImplementationOnce(() => new Promise(done => {resolve=done}))
  render()
  const signal=(fetchMock.mock.calls[0][1] as RequestInit).signal!
  hooks.visible=false
  expect(render()).toBe(false)
  expect(signal.aborted).toBe(true)
  resolve(Response.json(capabilities(true)))
  await inspection.mock.results[0].value
  expect(render()).toBe(false)
  fetchMock.mockResolvedValueOnce(Response.json(capabilities(true)))
  hooks.visible=true
  render()
  await inspection.mock.results[1].value
  expect(render()).toBe(true)
  expect(render(false)).toBe(false)
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

it('does not observe while inactive and keeps transport failures hidden until the next observation',async () => {
  expect(render(false)).toBe(false)
  expect(fetchMock).not.toHaveBeenCalled()
  fetchMock.mockRejectedValueOnce(new TypeError('offline'))
  render()
  expect(await inspection.mock.results[0].value).toMatchObject({ok:false})
  expect(render()).toBe(false)
  render(false)
  fetchMock.mockResolvedValueOnce(Response.json(capabilities(true)))
  render()
  await inspection.mock.results[1].value
  expect(render()).toBe(true)
})

it('invalidates an inactive or unmounted pending read even when transport ignores cancellation',async () => {
  let resolve!:(response:Response) => void
  fetchMock.mockImplementation(() => new Promise(done => {resolve=done}))
  render()
  const signal=(fetchMock.mock.calls[0][1] as RequestInit).signal!
  render(false)
  expect(signal.aborted).toBe(true)
  resolve(Response.json(capabilities(true)))
  await inspection.mock.results[0].value
  expect(render(false)).toBe(false)
  render()
  const second=(fetchMock.mock.calls[1][1] as RequestInit).signal!
  unmount()
  expect(second.aborted).toBe(true)
  resolve(Response.json(capabilities(true)))
  await inspection.mock.results[1].value
  expect(hooks.slots[0]).toBe(false)
})
