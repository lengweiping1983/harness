import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {api} from '@/lib/api'
import {getFileRoot} from '@/lib/fileRoot'
import {groupSessionsByWorkspace} from '@/lib/workspaces'
import {validPersistedDrafts} from '@/lib/draftPersistence'
import {useAppStore} from '../app'
import {useDraftStore} from '../drafts'

const project = {id: 'project', path: '/project', name: 'Project 原文', color: 'brand', exists: true}
const checkout = {cwd: project.path, mode: 'worktree' as const, ref: 'feature', expected_head: 'abc', expected_branch: 'main'}
const pending = <T,>() => {let resolve!: (value: T) => void; const promise = new Promise<T>(done => {resolve = done}); return {promise, resolve}}
beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key)})
  vi.stubGlobal('window', {confirm: vi.fn(() => true)})
  useDraftStore.setState({bySession: {}, submissions: {}})
  useAppStore.setState({activeSessionId: null, activeWorkspaceId: null, newSessionRevision: 1, newSessionCheckout: null,
    newSessionCheckoutStatus: 'idle', sessionCreating: false, sessions: [], workspaces: [project], workspacePickerOpen: false,
    defaultReasoningSave: null, toasts: []})
})
afterEach(() => {vi.restoreAllMocks(); vi.unstubAllGlobals(); useAppStore.setState({newSessionCheckout: null, newSessionCheckoutStatus: 'idle'})})

it('opens project selection for a new conversation without a project and preserves the unsent draft', async () => {
  useDraftStore.getState().setText('__unsaved__', '保留草稿')
  const write = vi.spyOn(api, 'createSession')
  useAppStore.getState().startNewSession(null)
  expect(useAppStore.getState()).toMatchObject({workspacePickerOpen: true, workspacePickerMode: 'select', activeSessionId: null})
  expect(getFileRoot()).toBeNull()
  expect(await useAppStore.getState().materializeSession()).toBe(false)
  expect(write).not.toHaveBeenCalled()
  expect(useDraftStore.getState().bySession.__unsaved__.text).toBe('保留草稿')
})

it('inherits the source project of a worktree conversation and waits for the new check', async () => {
  useAppStore.setState({activeSessionId: 'original', sessions: [{id: 'original', cwd: '/worktrees/one', metadata: {project_cwd: '/project'}}]})
  useAppStore.getState().startNewSession()
  expect(useAppStore.getState()).toMatchObject({activeWorkspaceId: 'project', newSessionCheckoutStatus: 'loading', workspacePickerOpen: false})
  expect(getFileRoot()).toBe('/project')
  const write = vi.spyOn(api, 'createSession')
  expect(await useAppStore.getState().materializeSession()).toBe(false)
  expect(write).not.toHaveBeenCalled()
})

it('sends the exact worktree intent only once and retains it in the recoverable draft', async () => {
  useAppStore.setState({activeWorkspaceId: 'project', newSessionCheckout: checkout, newSessionCheckoutStatus: 'ready'})
  const response = pending<Awaited<ReturnType<typeof api.createSession>>>()
  const write = vi.spyOn(api, 'createSession').mockReturnValue(response.promise)
  const creating = useAppStore.getState().materializeSession()
  expect(await useAppStore.getState().materializeSession()).toBe(false)
  const [body, requestId] = write.mock.calls[0]
  expect(body).toEqual({cwd: '/project', project: {workspace_id: 'project', mode: 'worktree', ref: 'feature', expected_head: 'abc', expected_branch: 'main'}})
  const raw = localStorage.getItem(useDraftStore.persist.getOptions().name!)!
  expect(validPersistedDrafts(raw)).toBe(true)
  expect(JSON.parse(raw).state.bySession.__unsaved__.creationRequest.project).toEqual(body!.project)
  response.resolve({ok: true, data: {session: {id: 'created', cwd: '/worktrees/new', metadata: {project_cwd: '/project', creation_request_id: requestId, creation_completed: true}}}})
  expect(await creating).toBe(true)
  expect(getFileRoot()).toBe('/worktrees/new')
  expect(write).toHaveBeenCalledTimes(1)
  expect(groupSessionsByWorkspace(useAppStore.getState().sessions, [project])[0].sessions[0].id).toBe('created')
})

it.each(['cancel', 'confirm'] as const)('%s confirms a local branch switch before accepting any write', async action => {
  vi.mocked(window.confirm).mockReturnValue(action === 'confirm')
  useAppStore.setState({activeWorkspaceId: 'project', newSessionCheckout: {...checkout, mode: 'local'}, newSessionCheckoutStatus: 'ready'})
  const write = vi.spyOn(api, 'createSession').mockImplementation(async (_body, id) => ({ok: true, data: {session: {id: 'created', metadata: {creation_request_id: id, creation_completed: true}}}}))
  expect(await useAppStore.getState().materializeSession()).toBe(action === 'confirm')
  expect(window.confirm).toHaveBeenCalledOnce()
  expect(write).toHaveBeenCalledTimes(action === 'confirm' ? 1 : 0)
})

it.each(['different', 'round-trip'] as const)('a %s checkout is not selected while the accepted submission stays with its original creation', async kind => {
  useAppStore.setState({activeWorkspaceId: 'project', newSessionCheckout: checkout, newSessionCheckoutStatus: 'ready'})
  const response = pending<Awaited<ReturnType<typeof api.createSession>>>()
  const write = vi.spyOn(api, 'createSession').mockReturnValue(response.promise)
  const creating = useAppStore.getState().materializeSession({id: 'original', text: 'Original', attachments: [], action: 'send', status: 'sending'})
  useAppStore.getState().setNewSessionCheckout({...checkout, ref: 'other'})
  if (kind === 'round-trip') useAppStore.getState().setNewSessionCheckout(checkout)
  response.resolve({ok: true, data: {session: {id: 'created', cwd: '/worktrees/new', metadata: {creation_request_id: write.mock.calls[0][1], creation_completed: true}}}})
  expect(await creating).toBe(true)
  expect(useAppStore.getState().activeSessionId).toBeNull()
  expect(useDraftStore.getState().submissions.created[0].text).toBe('Original')
})

it('an unconfirmed worktree preparation only reads on subsequent sends and never falls back', async () => {
  useAppStore.setState({activeWorkspaceId: 'project', newSessionCheckout: checkout, newSessionCheckoutStatus: 'ready'})
  const write = vi.spyOn(api, 'createSession').mockResolvedValue({ok: false, status: 503, code: 'session/workspace-uncertain', message: 'Unknown preparation'})
  const list = vi.spyOn(api, 'listSessions').mockResolvedValue({ok: true, data: {sessions: []}})
  expect(await useAppStore.getState().materializeSession()).toBe(false)
  expect(await useAppStore.getState().materializeSession()).toBe(false)
  expect(write).toHaveBeenCalledTimes(1)
  expect(list).toHaveBeenCalledTimes(2)
  expect(useDraftStore.getState().bySession.__unsaved__.creationRequest?.project?.mode).toBe('worktree')
})

it.each(['loading', 'error'] as const)('a %s project check blocks creation', async status => {
  useAppStore.setState({activeWorkspaceId: 'project', newSessionCheckoutStatus: status})
  const write = vi.spyOn(api, 'createSession')
  expect(await useAppStore.getState().materializeSession()).toBe(false)
  expect(write).not.toHaveBeenCalled()
})
