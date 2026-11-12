import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {api} from '../api'
import {readRequest, request} from '../http'

const fetchMock = vi.fn<typeof fetch>()
const catalog = {workspaces: [{id: 'project', path: '/project', name: 'Project 原文', color: 'blue'}], default: '/project'}
beforeEach(() => {vi.useFakeTimers(); fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock)})
afterEach(() => {vi.useRealTimers(); vi.unstubAllGlobals()})

it('retries connection refusal and restart proxy errors, then returns the actual project catalog', async () => {
  fetchMock.mockRejectedValueOnce(new TypeError('fetch failed'))
    .mockResolvedValueOnce(Response.json({error: 'restarting'}, {status: 503}))
    .mockResolvedValueOnce(new Response('proxy connection refused', {status: 500}))
    .mockResolvedValueOnce(Response.json(catalog))
  const pending = api.listWorkspaces({retry: true})
  await vi.runAllTimersAsync()
  expect(await pending).toEqual({ok: true, data: catalog})
  expect(fetchMock).toHaveBeenCalledTimes(4)
  expect(fetchMock.mock.calls.every(([path, options]) => path === '/api/v1/workspaces' && options?.method === 'GET')).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
})

it.each([
  [403, {error: 'denied'}, 'http/403'],
  [429, {error: 'rate limit'}, 'http/429'],
  [200, {}, 'response/invalid-schema'],
  [200, {workspaces: null, default: '/project'}, 'response/invalid-schema'],
  [200, {workspaces: [{id: 'project', path: 7, name: 'Project', color: 'blue'}], default: '/project'}, 'response/invalid-schema'],
] as const)('does not retry an explicit refusal or damaged protocol: %s', async (status, body, code) => {
  fetchMock.mockImplementation(async () => Response.json(body, {status}))
  expect(await api.listWorkspaces({retry: true})).toMatchObject({ok: false, status, code})
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})

it('stops after bounded backoff and preserves the final error instead of returning an empty catalog', async () => {
  fetchMock.mockImplementation(async () => Response.json({error: 'still unavailable'}, {status: 503}))
  const pending = api.listWorkspaces({retry: true})
  await vi.runAllTimersAsync()
  expect(await pending).toMatchObject({ok: false, status: 503, message: 'still unavailable'})
  expect(fetchMock).toHaveBeenCalledTimes(5)
  expect(vi.getTimerCount()).toBe(0)
})

it('cancels a retry delay without sending another GET', async () => {
  const controller = new AbortController()
  fetchMock.mockImplementation(async () => Response.json({error: 'restarting'}, {status: 503}))
  const pending = readRequest('/api/v1/workspaces', {signal: controller.signal})
  await vi.advanceTimersByTimeAsync(0)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  controller.abort()
  expect(await pending).toMatchObject({ok: false, status: 0, code: 'cancelled'})
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})

it('uses one total deadline rather than granting a fresh timeout to every retry', async () => {
  fetchMock.mockImplementation((_path, options) => new Promise((_resolve, reject) => {
    options!.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {once: true})
  }))
  const pending = readRequest('/api/v1/workspaces', {timeout: 100})
  await vi.advanceTimersByTimeAsync(100)
  expect(await pending).toMatchObject({ok: false, code: 'timeout'})
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})

it('keeps ordinary readback and all writes single-attempt unless a catalog read explicitly opts in', async () => {
  fetchMock.mockImplementation(async () => Response.json({error: 'restarting'}, {status: 503}))
  expect(await api.listWorkspaces()).toMatchObject({ok: false, status: 503})
  expect(await api.registerWorkspace({path: '/project'})).toMatchObject({ok: false, status: 503})
  expect(await request('/api/v1/workspaces/project', {method: 'DELETE'})).toMatchObject({ok: false, status: 503})
  expect(fetchMock.mock.calls.map(([, options]) => options?.method ?? 'GET')).toEqual(['GET', 'POST', 'DELETE'])
  expect(vi.getTimerCount()).toBe(0)
})
