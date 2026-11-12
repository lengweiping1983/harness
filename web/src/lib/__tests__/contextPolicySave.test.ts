import {expect, it, vi} from 'vitest'
import {createContextPolicySaves, type ContextPolicySnapshot} from '../contextPolicySave'
import type {ApiResult} from '../http'

const config: ContextPolicySnapshot['config'] = {tool_schema_mode: 'auto', context_auto_manage: true, context_working_budget: 10000, tool_result_max_chars: null, summary_max_tokens: null, output_reserve: 1000, context_window: 12000}
const success = (budget = 64000): ApiResult<ContextPolicySnapshot> => ({ok: true, data: {config: {...config, context_working_budget: budget}, effective: {context_window: 12000}}})
const failure = (status = 0): ApiResult<never> => ({ok: false, status, code: 'fixture', message: 'Original error 原文'})
type Transport = NonNullable<Parameters<typeof createContextPolicySaves>[0]>
const deferred = <T>() => {let resolve!: (value: T) => void; const promise = new Promise<T>(done => {resolve = done}); return {promise, resolve}}

it('keeps one immutable write across duplicate callers, edits and observer unmount; unknown saves only recheck', async () => {
  const write = deferred<ApiResult<ContextPolicySnapshot>>()
  const responses = [write.promise, Promise.resolve(failure()), Promise.resolve(success(32000)), Promise.resolve(success())]
  const transport = vi.fn((_path: string, _options?: {method?: string; body?: string}) => responses.shift()!)
  const announce = vi.fn(), saves = createContextPolicySaves(transport as Transport, announce)
  const observe = vi.fn(), unsubscribe = saves.subscribe(observe)
  const body = {context_working_budget: 64000}, owner = {}
  const pending = saves.save(body, owner, 3)
  body.context_working_budget = 32000
  await saves.save(body, {}, 4)
  expect(transport).toHaveBeenCalledTimes(1)
  expect(JSON.parse(transport.mock.calls[0][1]!.body!)).toEqual({context_working_budget: 64000})
  unsubscribe(); write.resolve(failure(503)); await pending
  expect(saves.getSnapshot()).toMatchObject({owner, draftRevision: 3, phase: 'unknown', body: {context_working_budget: 64000}})
  saves.clearResolved(); await saves.save(body, {}, 4)
  expect(transport).toHaveBeenCalledTimes(2)
  await saves.recheck()
  expect(saves.getSnapshot()?.phase).toBe('unknown')
  await saves.recheck()
  expect(saves.getSnapshot()).toMatchObject({phase: 'saved', owner, draftRevision: 3})
  expect(transport.mock.calls.map(call => call[1]?.method ?? 'GET')).toEqual(['PATCH', 'GET', 'GET', 'GET'])
  expect(announce).toHaveBeenCalledTimes(1)
  expect(observe).toHaveBeenCalledTimes(1)
})

it.each([400, 403, 409, 422])('explicit %s refusal permits a corrected intent without a readback', async status => {
  const transport = vi.fn().mockResolvedValueOnce(failure(status)).mockResolvedValueOnce(success()).mockResolvedValueOnce(success())
  const saves = createContextPolicySaves(transport as Transport)
  await saves.save({context_working_budget: 1}, {}, 0)
  expect(saves.getSnapshot()?.phase).toBe('failed'); expect(transport).toHaveBeenCalledTimes(1)
  saves.clearResolved(); expect(saves.getSnapshot()).toBeNull()
  await saves.save({context_working_budget: 64000}, {}, 1)
  expect(saves.getSnapshot()?.phase).toBe('saved')
  expect(transport.mock.calls.map(call => call[1]?.method ?? 'GET')).toEqual(['PATCH', 'PATCH', 'GET'])
})

it.each(['write', 'read'])('notifications during %s invalidate confirmation from the whole write/read operation', async phase => {
  const write = deferred<ApiResult<ContextPolicySnapshot>>(), read = deferred<ApiResult<ContextPolicySnapshot>>()
  const transport = vi.fn().mockReturnValueOnce(write.promise).mockReturnValueOnce(read.promise).mockResolvedValue(success())
  const announce = vi.fn(), saves = createContextPolicySaves(transport as Transport, announce)
  const pending = saves.save({context_working_budget: 64000}, {}, 0)
  if (phase === 'write') saves.notified()
  write.resolve(success())
  await vi.waitFor(() => expect(transport).toHaveBeenCalledTimes(2))
  if (phase === 'read') saves.notified()
  read.resolve(success()); await pending
  expect(saves.getSnapshot()).toMatchObject({phase: 'unknown', error: {key: 'ui.context.saveSuperseded'}})
  expect(announce).not.toHaveBeenCalled()
  await saves.recheck()
  expect(saves.getSnapshot()?.phase).toBe('saved'); expect(announce).toHaveBeenCalledTimes(1)
  expect(transport.mock.calls.filter(call => call[1]?.method === 'PATCH')).toHaveLength(1)
})

it.each([0, 408, 500, 503, 200])('ambiguous %s writes require authoritative confirmation', async status => {
  const transport = vi.fn().mockResolvedValueOnce(failure(status)).mockResolvedValueOnce(success())
  const saves = createContextPolicySaves(transport as Transport)
  await saves.save({context_working_budget: 64000}, {}, 0)
  expect(saves.getSnapshot()?.phase).toBe('saved'); expect(transport).toHaveBeenCalledTimes(2)
})
