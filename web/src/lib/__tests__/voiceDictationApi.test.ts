import {afterEach, expect, it, vi} from 'vitest'
import {api} from '../api'

afterEach(() => vi.unstubAllGlobals())

it('rejects missing or malformed dictation capabilities and opening receipts', async () => {
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  const valid = {available: true, cpu_supported: true, platform: 'windows', download_url: null, reason: null}
  const {cpu_supported: _cpu, ...missingCpu} = valid
  for (const data of [{}, missingCpu, {...valid, cpu_supported: 'true'}, {...valid, available: 'true'}, {...valid, platform: 'invented'}, {...valid, reason: {}}, {...valid, reason: 'ready'}]) {
    fetch.mockResolvedValueOnce(Response.json(data))
    expect(await api.voiceDictation()).toMatchObject({ok: false, code: 'response/invalid-schema'})
  }
  for (const data of [{}, {requested: false}, {requested: 'true'}, {requested: true, ready: true}]) {
    fetch.mockResolvedValueOnce(Response.json(data))
    expect(await api.openVoiceDictation()).toMatchObject({ok: false, code: 'response/invalid-schema'})
  }
  fetch.mockResolvedValueOnce(Response.json(valid))
  expect(await api.voiceDictation()).toEqual({ok: true, data: valid})
})

it('shares the pending opening and retains the same failure without replaying it', async () => {
  let resolve!: (response: Response) => void
  const fetch = vi.fn(() => new Promise<Response>(done => {resolve = done}))
  vi.stubGlobal('fetch', fetch)
  const first = api.openVoiceDictation(), second = api.openVoiceDictation()
  expect(first).toBe(second)
  expect(fetch).toHaveBeenCalledOnce()
  resolve(new Response('Unknown result', {status: 503}))
  expect(await first).toMatchObject({ok: false, status: 503})
  expect(await second).toBe(await first)
  expect(fetch).toHaveBeenCalledOnce()
})
