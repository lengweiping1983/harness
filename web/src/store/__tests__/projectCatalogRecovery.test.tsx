import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {api, loadCatalogs, type ApiFailure} from '@/lib/api'
import {setLocale, t} from '@/i18n'
import {ProjectSidebar} from '@/components/sidebar/ProjectSidebar'
import {WorkspacePicker} from '@/components/overlays/WorkspacePicker'
import {ReadinessCheck} from '@/components/chat/ReadinessCheck'
import {recoverProjectCatalog, useAppStore} from '../app'

vi.mock('@/lib/theme', async original => ({...await original<typeof import('@/lib/theme')>(), applyTheme: vi.fn()}))
vi.mock('@/lib/api', async original => ({...await original<typeof import('@/lib/api')>(), loadCatalogs: vi.fn()}))
vi.mock('react-dom', async original => ({...await original<typeof import('react-dom')>(), createPortal: (node: import('react').ReactNode) => node}))
// 服务端渲染验证消费同一份当前状态，不使用 Zustand 的初始空快照。
vi.mock('@/store/app', async original => {
  const actual = await original<typeof import('@/store/app')>()
  type State = ReturnType<typeof actual.useAppStore.getState>
  return {...actual, useAppStore: Object.assign((select: (state: State) => unknown) => select(actual.useAppStore.getState()), actual.useAppStore)}
})
const workspace = {id: 'project', path: '/project', name: 'Project 原文', color: 'blue'}
const session = {id: 'conversation', title: 'Conversation 原文', cwd: '/project'}
const failure: ApiFailure = {ok: false, status: 503, code: 'offline', message: 'Original failure', copy: {key: 'ui.http.invalidResponse'}}
const pageState = {hidden: false, documentElement: {style: {}}}
function pending<T>() {let resolve!: (value: T) => void; const promise = new Promise<T>(done => {resolve = done}); return {promise, resolve}}
const markup = () => renderToStaticMarkup(<ProjectSidebar />)

beforeEach(() => {
  vi.stubGlobal('localStorage', {getItem: () => null, setItem: vi.fn(), removeItem: vi.fn()})
  pageState.hidden = false
  vi.stubGlobal('document', pageState)
  vi.mocked(loadCatalogs).mockResolvedValue({status: null, tools: [], skills: [], workspace: null, errors: []})
  vi.spyOn(api, 'listSessions').mockResolvedValue({ok: true, data: {sessions: [session]}})
  vi.spyOn(api, 'listWorkspaces').mockResolvedValue(failure)
  vi.spyOn(api, 'models').mockResolvedValue(failure)
  vi.spyOn(api, 'settings').mockResolvedValue(failure)
  useAppStore.setState({booting: false, booted: false, bootError: null, activeSessionId: 'conversation', activeWorkspaceId: 'project',
    workspaces: [workspace], workspacesLoading: false, workspacesError: '', sessions: [session], sessionsLoading: false, sessionQuery: '', toasts: []})
})
afterEach(() => {vi.restoreAllMocks(); vi.unstubAllGlobals(); setLocale('zh-CN')})

it('retains confirmed projects after startup retry exhaustion and keeps the error resource through English Chinese English', async () => {
  await useAppStore.getState().boot()
  expect(api.listWorkspaces).toHaveBeenCalledWith({retry: true})
  expect(api.listSessions).toHaveBeenCalledWith({retry: true})
  expect(api.settings).toHaveBeenCalledWith({retry: true})
  expect(useAppStore.getState().workspaces).toEqual([workspace])
  expect(useAppStore.getState().workspacesError).toEqual(failure.copy)
  expect(useAppStore.getState().bootError).toBeNull()
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    expect(markup()).toContain('Project 原文')
    expect(markup()).toContain(t('ui.http.invalidResponse'))
    expect(markup()).toContain(t('ui.sidebar.retryProjects'))
    expect(markup()).not.toContain(t('ui.lib.sidebarGroups.097dcbb620db'))
  }
  expect(api.listWorkspaces).toHaveBeenCalledTimes(1)
})

it('recovers a failed cold-start catalog after service return and restores grouping and the conversation project', async () => {
  useAppStore.setState({workspaces: [], activeWorkspaceId: null})
  await useAppStore.getState().boot()
  expect(markup()).toContain('Conversation 原文')
  expect(markup()).toContain(t('ui.lib.sidebarGroups.19c6c208f94e'))
  expect(markup()).not.toContain(t('ui.lib.sidebarGroups.097dcbb620db'))
  const read = pending<Awaited<ReturnType<typeof api.listWorkspaces>>>()
  vi.mocked(api.listWorkspaces).mockReturnValueOnce(read.promise)
  const recovery = recoverProjectCatalog()
  expect(recoverProjectCatalog()).toBeUndefined()
  expect(api.listWorkspaces).toHaveBeenCalledTimes(2)
  expect(markup()).toContain(t('ui.sidebar.projectsRetrying'))
  expect(markup()).not.toContain(t('ui.store.app.a2de8299587b'))
  read.resolve({ok: true, data: {workspaces: [workspace], default: '/project'}})
  await recovery
  expect(useAppStore.getState().workspacesError).toBe('')
  expect(useAppStore.getState().activeWorkspaceId).toBe('project')
  expect(markup()).toContain('Project 原文')
  expect(markup()).not.toContain(t('ui.sidebar.retryProjects'))
  expect(recoverProjectCatalog()).toBeUndefined()
  expect(api.listWorkspaces).toHaveBeenCalledTimes(2)
})

it('does not observe hidden pages or override a project chosen while automatic recovery is pending', async () => {
  useAppStore.setState({workspacesError: failure.copy!, activeWorkspaceId: null})
  pageState.hidden = true
  expect(recoverProjectCatalog()).toBeUndefined()
  expect(api.listWorkspaces).not.toHaveBeenCalled()
  pageState.hidden = false
  const read = pending<Awaited<ReturnType<typeof api.listWorkspaces>>>()
  vi.mocked(api.listWorkspaces).mockReturnValueOnce(read.promise)
  const recovery = recoverProjectCatalog()
  useAppStore.getState().selectWorkspace('another-project')
  read.resolve({ok: true, data: {workspaces: [workspace], default: '/project'}})
  await recovery
  expect(useAppStore.getState().activeWorkspaceId).toBe('another-project')
  expect(api.listWorkspaces).toHaveBeenCalledTimes(1)
})

it('does not let an old failed startup response replace a newer successful project recovery', async () => {
  const old = pending<Awaited<ReturnType<typeof api.listWorkspaces>>>()
  vi.mocked(api.listWorkspaces).mockReturnValueOnce(old.promise).mockResolvedValueOnce({ok: true, data: {workspaces: [workspace], default: '/project'}})
  const boot = useAppStore.getState().boot()
  await useAppStore.getState().refreshWorkspaces()
  old.resolve(failure)
  await boot
  expect(useAppStore.getState().workspaces).toEqual([workspace])
  expect(useAppStore.getState().workspacesError).toBe('')
  expect(markup()).not.toContain(t('ui.sidebar.retryProjects'))
})

it('distinguishes a confirmed empty project registry from a failed catalog read', async () => {
  vi.mocked(api.listWorkspaces).mockResolvedValue({ok: true, data: {workspaces: [], default: '/project'}})
  await useAppStore.getState().boot()
  expect(useAppStore.getState().workspaces).toEqual([])
  expect(useAppStore.getState().workspacesError).toBe('')
  expect(markup()).toContain(t('ui.lib.sidebarGroups.097dcbb620db'))
  expect(markup()).not.toContain(t('ui.sidebar.retryProjects'))
})

it('marks missing projects in the picker and sidebar while retaining their conversations', () => {
  useAppStore.setState({workspaces: [{...workspace, exists: false}], workspacePickerOpen: true, workspacePickerMode: 'select'})
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const picker = renderToStaticMarkup(<WorkspacePicker />)
    expect(picker).toContain(t('ui.workspacePicker.missingDirectory'))
    expect(picker).toMatch(/<button[^>]*disabled=""[^>]*>.*?Project 原文.*?<\/button>/)
    expect(markup()).toContain(t('ui.workspacePicker.missingDirectory'))
    expect(markup()).toContain('Conversation 原文')
    expect(markup()).not.toContain(t('ui.components.sidebar.ProjectSidebar.e5fc62fa56e3'))
  }
  expect(useAppStore.getState().workspaces).toHaveLength(1)
})

it('renders readiness from unified credentials and draft conversations without changing user data', () => {
  useAppStore.setState({activeSessionId: null, sessions: [{id: 'empty', cwd: '/project', message_count: 0, metadata: {auto_title: 'awaiting'}}]})
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    for (const has_model_credentials of [true, false, undefined]) {
      useAppStore.setState({status: {config: {has_api_key: !has_model_credentials}, has_model_credentials}})
      const html = renderToStaticMarkup(<ReadinessCheck />)
      expect(html).toContain(t(has_model_credentials === undefined ? 'ui.readiness.modelUnknown' : has_model_credentials ? 'ui.readiness.model' : 'ui.readiness.noModel'))
      expect(html).toContain('/project')
    }
    expect(markup()).toContain(t('ui.sessionCreation.emptyDraft'))
    expect(markup()).not.toContain(t('ui.components.sidebar.ProjectSidebar.a91ae6035092'))
  }
  expect(useAppStore.getState().sessions[0]).toMatchObject({id: 'empty', message_count: 0, metadata: {auto_title: 'awaiting'}})
})
