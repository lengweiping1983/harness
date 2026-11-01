import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {api, type ApiFailure} from '@/lib/api'
import {noticeText} from '@/lib/noticeText'
import {reasoningSelection} from '@/lib/reasoningSelection'
import {ResourceError} from '@/lib/resourceError'
import {setLocale, t} from '@/i18n'
import {defaultReasoningBlocked, useAppStore} from '../app'
import {useDraftStore} from '../drafts'

const unknown: ApiFailure = {ok: false, status: 0, code: 'network', message: 'Original network', copy: {key: 'ui.http.invalidResponse'}}
const pending = <T,>() => {let resolve!: (value: T) => void; const promise = new Promise<T>(done => {resolve = done}); return {resolve, promise}}
const saved = (effort: string | null) => ({ok: true as const, data: {settings: {reasoning_effort: effort}, rejected: []}})
beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key)})
  setLocale('en')
  useDraftStore.setState({bySession: {}, submissions: {}})
  useAppStore.setState({activeSessionId: null, activeWorkspaceId: 'project', newSessionCheckout: null, newSessionCheckoutStatus: 'ready', newSessionRevision: 1, sessionCreating: false,
    sessions: [{id: 'other', reasoning_effort: 'low', metadata: {reasoning_effort: 'low'}}], workspaces: [{id:'project',path:'/fixture',name:'Project',color:'brand'}], workspace: null,
    defaultReasoningEffort: 'low', defaultReasoningSave: null, reasoningSaves: {}, toasts: []})
})
afterEach(() => {vi.restoreAllMocks(); vi.unstubAllGlobals(); setLocale('en')})

it.each(['high', null])('chooses default %s without creating or changing another conversation', async effort => {
  const write = vi.spyOn(api, 'updateSettings').mockResolvedValue(saved(effort))
  const create = vi.spyOn(api, 'createSession'), sessionWrite = vi.spyOn(api, 'updateSession')
  await useAppStore.getState().setReasoningEffort(effort)
  expect(write).toHaveBeenCalledExactlyOnceWith({reasoning_effort: effort})
  expect(reasoningSelection(undefined, useAppStore.getState().defaultReasoningEffort).value).toBe(effort)
  expect(useAppStore.getState().defaultReasoningSave).toEqual({pending: false, error: null, unconfirmed: false})
  expect(useAppStore.getState().sessions[0].reasoning_effort).toBe('low')
  expect(create).not.toHaveBeenCalled(); expect(sessionWrite).not.toHaveBeenCalled()
})

it('serializes rapid choices and waits for the latest confirmed default before creation', async () => {
  const first = pending<Awaited<ReturnType<typeof api.updateSettings>>>()
  const second = pending<Awaited<ReturnType<typeof api.updateSettings>>>()
  const write = vi.spyOn(api, 'updateSettings').mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
  const create = vi.spyOn(api, 'createSession').mockImplementation(async (_body, requestId) => {
    expect(useAppStore.getState().defaultReasoningEffort).toBe('high')
    expect(useAppStore.getState().defaultReasoningSave?.pending).toBe(false)
    return {ok: true, data: {session: {id: 'created', metadata: {creation_request_id: requestId, creation_completed: true}}}}
  })
  const medium = useAppStore.getState().setReasoningEffort('medium')
  const high = useAppStore.getState().setReasoningEffort('high')
  const creation = useAppStore.getState().materializeSession()
  await Promise.resolve()
  expect(write).toHaveBeenCalledTimes(1); expect(create).not.toHaveBeenCalled()
  first.resolve(saved('medium')); await medium; await Promise.resolve()
  expect(write).toHaveBeenCalledTimes(2); expect(create).not.toHaveBeenCalled()
  expect(useAppStore.getState().defaultReasoningEffort).toBe('high')
  await useAppStore.getState().setReasoningEffort('ultra')
  expect(write).toHaveBeenCalledTimes(2)
  second.resolve(saved('high')); await high
  expect(await creation).toBe(true)
  expect(create).toHaveBeenCalledTimes(1)
})

it.each(['refused', 'persist-failed', 'unknown'] as const)('%s save cannot silently send with an unconfirmed choice', async kind => {
  const write = vi.spyOn(api, 'updateSettings').mockResolvedValue({...unknown, status: kind === 'refused' ? 403 : kind === 'persist-failed' ? 503 : 0, code: kind === 'persist-failed' ? 'settings/persist-failed' : unknown.code})
  const read = vi.spyOn(api, 'settings').mockResolvedValue(unknown)
  const create = vi.spyOn(api, 'createSession')
  await useAppStore.getState().setReasoningEffort('high')
  expect(await useAppStore.getState().materializeSession()).toBe(false)
  expect(create).not.toHaveBeenCalled(); expect(write).toHaveBeenCalledTimes(1)
  expect(read).toHaveBeenCalledTimes(kind === 'unknown' ? 1 : 0)
  expect(useAppStore.getState().defaultReasoningEffort).toBe(kind === 'unknown' ? 'high' : 'low')
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    expect(noticeText(useAppStore.getState().defaultReasoningSave!.error!)).toBe(t('ui.http.invalidResponse'))
    expect(noticeText(useAppStore.getState().toasts.at(-1)!.title)).toBe(t('ui.models.reasoningBeforeSend'))
  }
})

it.each(['lost', 'mismatched', 'malformed', 'exception'] as const)('%s receipt is confirmed by one read without replay', async kind => {
  const write = vi.spyOn(api, 'updateSettings')
  if (kind === 'exception') write.mockRejectedValue(new ResourceError({key: 'ui.http.invalidResponse'}))
  else write.mockResolvedValue(kind === 'mismatched' ? saved('low') : {...unknown, ...(kind === 'malformed' ? {status: 200, code: 'response/invalid-json'} : {})})
  const read = vi.spyOn(api, 'settings').mockResolvedValue(saved('high'))
  await useAppStore.getState().setReasoningEffort('high')
  expect(useAppStore.getState()).toMatchObject({defaultReasoningEffort: 'high', defaultReasoningSave: {pending: false, error: null, unconfirmed: false}})
  expect(write).toHaveBeenCalledTimes(1); expect(read).toHaveBeenCalledTimes(1)
})

it('readback restores the actual default and retains bilingual feedback when it differs', async () => {
  vi.spyOn(api, 'updateSettings').mockResolvedValue(unknown)
  vi.spyOn(api, 'settings').mockResolvedValue(saved(null))
  await useAppStore.getState().setReasoningEffort('high')
  expect(useAppStore.getState().defaultReasoningEffort).toBeNull()
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    expect(noticeText(useAppStore.getState().defaultReasoningSave!.error!)).toBe(t('ui.store.app.340d62606aa1', {p0: t('ui.http.invalidResponse')}))
  }
})

it.each(['other', 'new-draft'] as const)('waiting creation cannot select or submit into %s after navigation', async target => {
  const response = pending<Awaited<ReturnType<typeof api.updateSettings>>>()
  vi.spyOn(api, 'updateSettings').mockReturnValue(response.promise)
  const create = vi.spyOn(api, 'createSession')
  const save = useAppStore.getState().setReasoningEffort('high')
  const creation = useAppStore.getState().materializeSession()
  if (target === 'other') useAppStore.getState().selectSession('other')
  else useAppStore.getState().startNewSession(null)
  response.resolve(saved('high')); await save
  expect(await creation).toBe(false)
  expect(create).not.toHaveBeenCalled()
  expect(useAppStore.getState().sessions[0].reasoning_effort).toBe('low')
  expect(useAppStore.getState().reasoningSaves).toEqual({})
})

it('a pending or unknown default blocks inherited conversations while explicit overrides stay independent', async () => {
  useAppStore.setState({sessions: [...useAppStore.getState().sessions, {id: 'inherited'}]})
  const response = pending<Awaited<ReturnType<typeof api.updateSettings>>>()
  vi.spyOn(api, 'updateSettings').mockReturnValue(response.promise)
  vi.spyOn(api, 'settings').mockResolvedValue(unknown)
  const save = useAppStore.getState().setReasoningEffort('high')
  expect(defaultReasoningBlocked('inherited')).toBe(true)
  expect(defaultReasoningBlocked('other')).toBe(false)
  response.resolve(unknown); await save
  expect(defaultReasoningBlocked('inherited')).toBe(true)
  expect(defaultReasoningBlocked('other')).toBe(false)
})

it('freezes Agent and execution mode before awaiting a default save and preserves later choices', async () => {
  const response = pending<Awaited<ReturnType<typeof api.updateSettings>>>()
  vi.spyOn(api, 'updateSettings').mockReturnValue(response.promise)
  useDraftStore.getState().setAgentSelection('__unsaved__', {name: 'original', agent_type: 'agent'})
  useDraftStore.getState().setExecutionMode('__unsaved__', 'plan')
  const create = vi.spyOn(api, 'createSession').mockImplementation(async (_body, requestId) => ({ok: true, data: {
    session: {id: 'created', metadata: {creation_request_id: requestId, creation_completed: true}},
    agent_mount: {available: true, agent: 'original', revision: 1},
  }}))
  const save = useAppStore.getState().setReasoningEffort('high')
  const creation = useAppStore.getState().materializeSession({id: 'submission', text: 'Original', attachments: [], action: 'send', status: 'sending'})
  useDraftStore.getState().setAgentSelection('__unsaved__', {name: 'later', agent_type: 'agent'})
  useDraftStore.getState().setExecutionMode('__unsaved__', 'goal')
  response.resolve(saved('high')); await save
  expect(await creation).toBe(true)
  expect(create).toHaveBeenCalledExactlyOnceWith({cwd: '/fixture', agent_selection: 'original'}, expect.any(String))
  expect(useDraftStore.getState().bySession.__unsaved__).toMatchObject({agentSelection: {name: 'later'}, executionMode: 'goal'})
})

it.each([false, true])('an optimistic session override cannot bypass an unknown default (receipt unknown: %s)', async unconfirmed => {
  useAppStore.setState({activeSessionId: 'inherited', sessions: [{id: 'inherited'}], defaultReasoningSave: {pending: false, error: unknown.copy!, unconfirmed: true}})
  const response = pending<Awaited<ReturnType<typeof api.updateSession>>>()
  vi.spyOn(api, 'updateSession').mockReturnValue(response.promise)
  vi.spyOn(api, 'getSession').mockResolvedValue(unknown)
  const save = useAppStore.getState().setReasoningEffort(null)
  expect(defaultReasoningBlocked('inherited')).toBe(true)
  response.resolve(unconfirmed ? unknown : {ok: true, data: {session: {id: 'inherited', metadata: {reasoning_effort: ''}}}})
  await save
  expect(defaultReasoningBlocked('inherited')).toBe(unconfirmed)
})
