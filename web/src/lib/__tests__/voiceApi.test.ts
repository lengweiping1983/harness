import {afterEach, expect, it, vi} from 'vitest'
import {prepareVoiceInput, voiceRequest} from '../voiceApi'

afterEach(() => {vi.unstubAllGlobals(); vi.useRealTimers()})

it('keeps cold preparation pending beyond a probe timeout until the actual ready reply without replay',async()=>{
  vi.useFakeTimers()
  let ready!:(response:Response)=>void
  const fetch=vi.fn().mockImplementation(()=>new Promise<Response>(resolve=>{ready=resolve}))
  vi.stubGlobal('fetch',fetch)
  let completed=false
  const preparation=prepareVoiceInput().then(result=>{completed=true;return result})
  await vi.advanceTimersByTimeAsync(15001)
  expect(completed).toBe(false)
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(false)
  ready(Response.json({ready:true}))
  expect(await preparation).toEqual({ok:true,data:{ready:true}})
  expect(fetch).toHaveBeenCalledOnce()
  expect(vi.getTimerCount()).toBe(0)
})

it('validates preparation confirmation and never retries a failed or uncertain request',async () => {
  const fetch=vi.fn()
  vi.stubGlobal('fetch',fetch)
  for(const body of [{},{ready:false},{ready:'true'},{ready:true,extra:1}]) {
    fetch.mockResolvedValueOnce(Response.json(body))
    expect(await prepareVoiceInput()).toMatchObject({ok:false,code:'response/invalid-schema'})
  }
  expect(fetch).toHaveBeenCalledTimes(4)
  fetch.mockResolvedValueOnce(Response.json({ready:true}))
  expect(await prepareVoiceInput()).toEqual({ok:true,data:{ready:true}})
  expect(fetch.mock.calls.at(-1)?.[0]).toBe('/api/v1/voice/preparations')
})

it.each(['/api/v1/voice/dictation','/api/v1/voice/preparations','/api/v1/voice/transcriptions'])('retains missing-route diagnostics for %s with an explicit language resource',async path => {
  const fetch=vi.fn().mockResolvedValue(Response.json({code:'route/not-found',message:'raw route diagnostic'},{status:404}))
  vi.stubGlobal('fetch',fetch)
  expect(await voiceRequest(path)).toMatchObject({ok:false,status:404,code:'route/not-found',copy:{key:'ui.voice.backendMismatch'},details:{copy:{key:'ui.voice.backendMismatch'},reason:'raw route diagnostic'}})
  expect(fetch).toHaveBeenCalledOnce()
})
