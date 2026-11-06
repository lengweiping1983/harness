import {createInflightOperations} from '@/lib/inflightOperations'
import {createdFor, creationUnconfirmed, creationRetained, recoverSessionCreation} from '@/lib/sessionCreation'
import type {ConversationCheckout} from '@/lib/conversationProject'
import {captureSessionLocalDrafts, sameSessionLocalDrafts} from '@/lib/sessionLocalDrafts'
import {download} from '@/lib/utils'
import {INSPECTOR_PANELS} from '@/lib/inspectorPanels'
import { isUnconfirmedWrite, type ApiResult } from '@/lib/http'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { applyTheme, resolveTheme, type Theme } from '@/lib/theme'
export type { Theme } from '@/lib/theme'
import { t, prepareLocale, setLocale, getLocale, type LanguagePreference, type Locale } from '@/i18n'
import { create } from 'zustand'
import type {
  ResourceText,
  InspectorTab,
  UiSettings,
  ModelCatalog,
  ModelInfo,
  SessionSummary,
  SkillInfo,
  StatusPayload,
  ToolSchema,
  WorkspaceInfo,
  WorkspacePayload,
} from '@/types'
import { api, loadCatalogs, type ApiFailure } from '@/lib/api'
import { useChatStore } from './chat'
import { useDraftStore, type PendingSubmission } from './drafts'
import { useGitDraftStore } from './gitDrafts'
import { getFileRoot, setFileRoot } from '@/lib/fileRoot'
import { matchWorkspaceFor, newSessionDirectory, sessionDirectory, sessionProjectDirectory } from '@/lib/workspaces'
import type { LayoutMode } from '@/lib/layout'
import { toastDuration } from '@/lib/toastTimer'
import { reasoningSelection } from '@/lib/reasoningSelection'
import { closeFilePreview, filePreviewScope, rememberFilePreview, sameFileReference, type FilePreviewReference } from '@/lib/filePreviewHistory'

/** 全局应用状态：主题、会话列表、目录数据、检查器、通知。 */

export type SettingsTarget = import('@/lib/settingsCatalog').SettingsPage | 'roles' | 'teams'
export const modelSaveKey = (sessionId: string | null) => JSON.stringify([sessionId])
/** 只有明确拒绝或明确落盘失败才能排除写入；成功状态中的损坏回执仍需回查。 */
export const modelWriteUnconfirmed = (failure: ApiFailure) => failure.code !== 'settings/persist-failed' && isUnconfirmedWrite(failure)
export const nameSaveKey = (kind: 'session' | 'workspace', id: string) => JSON.stringify([kind, id])

/** 继承默认值的对话不能按尚未确认的强度提交；显式覆盖保持独立。 */
export function defaultReasoningBlocked(sessionId: string | null): boolean {
  const state = useAppStore.getState()
  const save = state.defaultReasoningSave
  const sessionSave = sessionId ? state.reasoningSaves[sessionId] : undefined
  return (reasoningSelection(state.sessions.find(session => session.id === sessionId), state.defaultReasoningEffort).inherited
    || sessionSave?.pending || sessionSave?.unconfirmed) === true
    && Boolean(save?.pending || save?.error || save?.unconfirmed)
}

/** 身份失配或未知回执只读回查；确认原条目已不存在前保留本地状态。 */
async function confirmDeletion(result: ApiResult<{deleted: string}>, id: string, readAbsent: () => Promise<boolean>): Promise<'confirmed' | 'rejected' | 'unconfirmed'> {
  if (result.ok && result.data.deleted === id) return 'confirmed'
  if (!result.ok && !isUnconfirmedWrite(result)) return 'rejected'
  try { if (await readAbsent()) return 'confirmed' }
  catch { /* 读取失败不能证明原写入成功或被拒绝，不重放。 */ }
  return 'unconfirmed'
}

/** Keep confirmed names visible; drafts and save feedback belong to the editor. */
async function saveName(kind: 'session' | 'workspace', id: string, draft: string): Promise<ApiFailure | null> {
  const name = draft.trim()
  if (!name) return { ok: false, status: 400, code: 'rename/empty', message: t("ui.store.app.090ec5f82dfc"), copy: {key: "ui.store.app.090ec5f82dfc"} }
  const key = nameSaveKey(kind, id)
  if (useAppStore.getState().nameSaves[key]) return { ok: false, status: 409, code: 'rename/busy', message: t("ui.store.app.232d99bbaa75"), copy: {key: "ui.store.app.232d99bbaa75"} }
  useAppStore.setState((state) => ({ nameSaves: { ...state.nameSaves, [key]: true } }))
  const publish = () => useAppStore.setState((state) => kind === 'session'
    ? { sessions: state.sessions.map((item) => item.id === id ? { ...item, title: name } : item) }
    : { workspaces: state.workspaces.map((item) => item.id === id ? { ...item, name } : item) })
  try {
    const result = kind === 'session' ? await api.updateSession(id, { title: name }) : await api.updateWorkspace(id, { name })
    if (result.ok) {
      const data = result.data as { session?: SessionSummary; workspace?: WorkspaceInfo }
      const record = kind === 'session' ? data.session : data.workspace
      if (record?.id === id && (kind === 'session' ? data.session?.title : data.workspace?.name) === name) { publish(); return null }
    } else if (result.code === 'workspace/persist-failed' || !isUnconfirmedWrite(result)) return result
    // A lost response may follow a committed write. Read before offering a retry.
    if (kind === 'session') {
      const read = await api.getSession(id, 'conversation-window')
      if (read.ok && read.data.header.id === id && read.data.header.title === name) { publish(); return null }
    } else {
      const read = await api.listWorkspaces()
      if (read.ok && read.data.workspaces.some((item) => item.id === id && item.name === name)) { publish(); return null }
    }
    return { ok: false, status: 0, code: 'rename/unconfirmed', message: t("ui.store.app.8e4a2e5a35b6"), copy: {key: "ui.store.app.8e4a2e5a35b6"} }
  } catch {
    return { ok: false, status: 0, code: 'rename/unconfirmed', message: t("ui.store.app.8e4a2e5a35b6"), copy: {key: "ui.store.app.8e4a2e5a35b6"} }
  } finally {
    useAppStore.setState((state) => { const nameSaves = { ...state.nameSaves }; delete nameSaves[key]; return { nameSaves } })
  }
}

export interface Toast {
  id: string
  title: ResourceText
  description?: ResourceText
  tone: 'info' | 'success' | 'warn' | 'danger'
  /** 可选的行动按钮，例如"重试"。 */
  action?: { label: ResourceText; run: () => void }
  duration?: number
  persistent?: boolean
}

let languageEpoch = 0
let catalogLanguageEpoch = 0
let settingsStatusRequest = 0

interface AppState {
  language: LanguagePreference
  effectiveLanguage: Locale
  systemRegion: string | null
  detectionSource: string
  languageSaving: boolean
  languageError: ResourceText | null
  refreshLanguage: () => Promise<void>
  setLanguage: (language: LanguagePreference) => Promise<void>
  theme: Theme
  booted: boolean
  booting: boolean
  bootError: ResourceText | null

  sessions: SessionSummary[]
  sessionsLoading: boolean
  activeSessionId: string | null
  /** 区分同一项目内多次进入的新会话，隔离尚未返回的创建请求。 */
  newSessionRevision: number
  newSessionCheckout: ConversationCheckout | null
  newSessionCheckoutRevision: number
  newSessionCheckoutStatus: 'idle' | 'loading' | 'ready' | 'error'
  setNewSessionCheckout: (value: ConversationCheckout | null, status?: 'loading' | 'ready' | 'error') => void

  /**
   * 项目（Workspace）：会话之上的目录分组层。
   *
   * 之前只有"单一工作区根 + 扁平会话列表"，会话一多就没法看了 ——
   * 缺的不是样式，是这一层领域模型。有了它，侧栏分组、新建会话带 cwd、
   * 文件浏览跟随项目全都自然成立。
   */
  workspaces: WorkspaceInfo[]
  workspacesLoading: boolean
  workspacesError: ResourceText
  activeWorkspaceId: string | null

  /** 模型目录：composer 的 model seat、/model 菜单、设置页共用。 */
  models: ModelInfo[]
  modelProviders: string[]
  defaultModel: string
  defaultReasoningEffort: string | null
  /** 模型 id -> 是否在选择器里可见（设置面板勾选）。 */
  modelVisibility: Record<string, boolean>
  modelVisibilitySaves: Record<string, { pending: boolean; target: boolean; error: ResourceText | null; unconfirmed: boolean }>

  status: StatusPayload | null
  tools: ToolSchema[]
  skills: SkillInfo[]
  workspace: WorkspacePayload | null
  sessionQuery: string

  sidebarOpen: boolean
  inspectorOpen: boolean
  inspectorExpanded: boolean
  layoutMode: LayoutMode
  /** 窄屏下用户显式点开的抽屉；null = 未做显式选择，交给解算器回退。 */
  drawerIntent: 'none' | 'left' | 'right' | null
  inspectorTab: InspectorTab
  commandOpen: boolean
  conversationSearchOpen: boolean
  quickOpen: boolean
  shortcutsOpen: boolean
  /** 设置面板（分栏：通用 / 模型 / 快捷键）。 */
  settingsOpen: boolean
  settingsTarget: SettingsTarget | null
  settingsWorkspaceRoot: string | null
  /** 项目选择器：目录浏览器。 */
  workspacePickerOpen: boolean
  workspacePickerMode: 'add' | 'select'
  /** 模型菜单（model seat 与 /model 共用同一个入口状态）。 */
  modelMenuOpen: boolean
  reasoningSaves: Record<string, { pending: boolean; error: ResourceText | null; unconfirmed: boolean }>
  defaultReasoningSave: { pending: boolean; error: ResourceText | null; unconfirmed: boolean } | null
  modelSaves: Record<string, boolean>
  nameSaves: Record<string, boolean>
  defaultModelSave: { target: string; error: ResourceText | null; unconfirmed: boolean } | null
  /** 内容字号（px），允许 10–22。 */
  uiFontSize: number
  lightboxSrc: { src: string; alt?: string; images?: { src: string; alt?: string }[]; index?: number } | null

  /** 终端摘录携带来源会话与投递序号；仅原会话消费，旧回执不能清除新摘录。 */
  pendingQuote: { seq: number; sessionId: string; text: string } | null

  toasts: Toast[]

  boot: () => Promise<void>
  refreshSessions: () => Promise<boolean>
  selectSession: (id: string | null) => void
  startNewSession: (workspaceId?: string | null) => void
  sessionCreating: boolean
  materializeSession: (submission?: PendingSubmission) => Promise<boolean>
  refreshWorkspaces: () => Promise<void>
  selectWorkspace: (id: string | null) => void
  addWorkspace: (path: string, name?: string) => Promise<WorkspaceInfo | null>
  renameWorkspace: (id: string, name: string) => Promise<ApiFailure | null>
  removeWorkspace: (id: string) => Promise<void>
  setSessionModel: (sessionId: string, model: string | null) => Promise<ApiFailure | null>
  setReasoningEffort: (effort: string | null, inherit?: boolean) => Promise<void>
  setDefaultModel: (model: string) => Promise<ApiFailure | null>
  dismissDefaultModelFeedback: () => void
  setModelVisibility: (modelId: string, visible: boolean) => Promise<void>
  removeSession: (id: string) => Promise<void>
  renameSession: (id: string, title: string) => Promise<ApiFailure | null>
  renameLocal: (id: string, title: string) => void
  setSessionQuery: (q: string) => void
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
  toggleSidebar: () => void
  toggleInspector: () => void
  toggleInspectorExpanded: () => void
  /** 窄屏抽屉动作：打开指定抽屉（已开着则收起）。 */
  toggleDrawer: (which: 'left' | 'right') => void
  closeDrawer: () => void
  setLayoutMode: (mode: LayoutMode) => void
  setInspectorTab: (tab: InspectorTab) => void
  inspectorRunTarget: { sessionId: string; turn?: number; runId?: string; seq: number } | null
  revealRun: (sessionId: string, target: { turn: number } | { runId: string }) => void
  dismissRunPreview: () => void
  inspectorMemberTarget: { owner: string; id: string; name: string; agent?: string; role?: string; leadName?: string; leadAgent?: string; contextSessionId?: string; preview?: { text: string; truncated: boolean } } | null
  inspectorMemberTabs: Record<string, { target: NonNullable<AppState['inspectorMemberTarget']>; origin: HTMLElement | null }>
  closeMemberTab: (tab: InspectorTab) => void
  inspectorMemberOrigin: HTMLElement | null
  revealMember: (target: NonNullable<AppState['inspectorMemberTarget']>, origin?: HTMLElement) => void
  dismissMember: () => void
  inspectorJobTarget: { sessionId: string; jobId: string; seq: number } | null
  revealJob: (sessionId: string, jobId: string) => void
  inspectorBrowserTarget: { sessionId: string; url: string; seq: number } | null
  revealBrowser: (sessionId: string, url: string) => void
  inspectorTerminalTarget: { sessionId: string; terminalId: string; seq: number } | null
  revealTerminal: (sessionId: string, terminalId: string) => void
  inspectorChangesTarget: { sessionId: string; turn: number; seq: number; path?: string } | null
  revealTurnChanges: (sessionId: string, turn: number, path?: string) => void
  dismissTurnChanges: () => void
  /**
   * 跨组件的「打开这个文件」投递点。
   *
   * 交付物卡片、diff 视图、工具结果里的路径片段都在消息流里，而文件浏览在
   * 右侧检查器里 —— 两者之间需要一个 store 级的单向通道。带 ``seq`` 是为了
   * 连续点同一个文件两次也能生效：只看 path 的话第二次会被当成重复投递丢掉。
   */
  inspectorFileTarget: { path: string; seq: number; sessionId: string | null; root?: string | null; directory?: boolean; focusContent?: boolean; lineRange?: { start: number; end?: number } } | null
  inspectorFileHistory: Record<string, FilePreviewReference[]>
  dismissFilePreview: () => void
  closeFileTab: (reference: FilePreviewReference) => void
  revealInFiles: (path: string, lineRange?: { start: number; end?: number }, source?: { root: string | null; directory?: boolean; focusContent?: boolean }) => void
  setCommandOpen: (open: boolean) => void
  setConversationSearchOpen: (open: boolean) => void
  setQuickOpen: (open: boolean) => void
  setShortcutsOpen: (open: boolean) => void
  setSettingsOpen: (open: boolean, target?: SettingsTarget, root?: string) => void
  setWorkspacePickerOpen: (open: boolean, mode?: 'add' | 'select') => void
  setModelMenuOpen: (open: boolean) => void
  setUiFontSize: (size: number) => void
  openLightbox: (src: string, alt?: string) => void
  openImageGallery: (images: { src: string; alt?: string }[], index?: number) => void
  stepLightbox: (delta: number) => void
  closeLightbox: () => void
  quoteInto: (sessionId: string, text: string) => void
  clearQuote: (seq: number) => void
  toast: (toast: Omit<Toast, 'id'>) => string
  dismissToast: (id: string) => void
}

/* 浏览器本地保存主题和字号偏好。 */
const deleteOnce = createInflightOperations()

let sessionListRequest = 0
let workspaceListRequest = 0
const THEME_KEY = 'harness.ui-theme'
const FONT_SIZE_KEY = 'harness.ui-font-size'

function readStoredTheme(): Theme {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    if (saved === 'dark' || saved === 'light' || saved === 'system') return saved
  } catch {
    /* 隐私模式下 localStorage 会抛，忽略 */
  }
  return 'system'
}

function readInspectorTab(): InspectorTab {
  try {
    const value = localStorage.getItem('harness.inspector-tab.v1')
    return INSPECTOR_PANELS.find(panel => panel.value === value)?.value ?? 'home'
  } catch { return 'home' }
}

function readStoredFontSize(): number {
  try {
    const raw = Number(localStorage.getItem(FONT_SIZE_KEY))
    if (Number.isFinite(raw) && raw >= MIN_FONT_SIZE && raw <= MAX_FONT_SIZE) return Math.round(raw)
  } catch {
    /* 忽略 */
  }
  return DEFAULT_FONT_SIZE
}

export const MIN_FONT_SIZE = 10
export const MAX_FONT_SIZE = 22
export const DEFAULT_FONT_SIZE = 16

function applyFontSize(size: number) {
  const clamped = Number.isFinite(size) ? Math.min(MAX_FONT_SIZE, Math.max(MIN_FONT_SIZE, Math.round(size))) : DEFAULT_FONT_SIZE
  document.documentElement.style.fontSize = clamped + 'px'
  try {
    localStorage.setItem(FONT_SIZE_KEY, String(clamped))
  } catch {
    /* 忽略写入失败 */
  }
  return clamped
}

let toastSeq = 0
let revealSeq = 0

let quoteSeq = 0

type ReasoningQueue = { ticket: number; confirmed: string | null; confirmedOverride: boolean; uncertain: boolean; tail: Promise<void> }
const reasoningQueues = new Map<string | null, ReasoningQueue>()

function validLanguageSettings(settings: UiSettings): boolean {
  return ['auto', 'zh-CN', 'en'].includes(String(settings.language)) && ['zh-CN', 'en'].includes(String(settings.effective_language))
}
async function receiveLanguage(settings: UiSettings): Promise<boolean> {
  if (!validLanguageSettings(settings)) return false
  const ticketBeforeLoad = languageEpoch
  const previous = useAppStore.getState()
  const changed = getLocale() !== settings.effective_language
  try {await prepareLocale(settings.effective_language as Locale)} catch {
    if (ticketBeforeLoad === languageEpoch) useAppStore.setState({language: settings.language as LanguagePreference, languageError: {key: 'settings.language.loadFailed'}})
    return false
  }
  if (ticketBeforeLoad !== languageEpoch) return false
  useAppStore.setState({ language: settings.language as LanguagePreference, effectiveLanguage: settings.effective_language as Locale,
    systemRegion: settings.system_region ?? null, detectionSource: settings.detection_source ?? 'unavailable', languageError: null })
  // 已确认资源就绪后在同一任务内发布状态和语言，避免中间帧。
  void setLocale(settings.effective_language as Locale)
  if (changed || previous.language !== settings.language) {
    try { localStorage.setItem('harness.language.changed.v1', String(Date.now()) + Math.random()) } catch { /* Socket and focus synchronization remain available. */ }
  }
  if (changed) {
    // Catalog prose is rendered by the host; refresh it without touching chat drafts or history.
    const ticket = ++catalogLanguageEpoch
    void Promise.all([api.tools(), api.skills(), api.models()]).then(([tools, skills, models]) => {
      if (ticket !== catalogLanguageEpoch || getLocale() !== settings.effective_language) return
      useAppStore.setState((state) => ({
        ...(tools.ok ? { tools: tools.data.tools } : {}),
        ...(skills.ok ? { skills: skills.data.skills } : {}),
        ...(models.ok ? { models: state.models.map((model) => {
          const translated = models.data.models.find((item) => item.id === model.id)
          return translated ? { ...model, label: translated.label, description: translated.description } : model
        }) } : {}),
      }))
    }).catch(() => { /* Existing catalogs remain usable while disconnected. */ })
  }
  return true
}

export const useAppStore = create<AppState>((set, get) => ({
  language: 'auto', effectiveLanguage: getLocale(), systemRegion: null, detectionSource: 'unavailable', languageSaving: false, languageError: null,
  refreshLanguage: async () => {
    if (get().languageSaving) return
    const ticket = ++languageEpoch
    try { const read = await api.settings(); if (read.ok && ticket === languageEpoch && validLanguageSettings(read.data.settings)) { await receiveLanguage(read.data.settings) } } catch { /* Retain confirmed settings while offline. */ }
  },
  setLanguage: async (language) => {
    if (get().languageSaving) return
    const ticket = ++languageEpoch
    set({ languageSaving: true, languageError: null })
    try {
      const result = await api.updateSettings({ language })
      if (ticket !== languageEpoch) return
      if (result.ok && !result.data.rejected?.includes('language') && result.data.settings.language === language && validLanguageSettings(result.data.settings)) {
        await receiveLanguage(result.data.settings)
        return
      }
      if (!result.ok && result.code === 'settings/persist-failed') {
        set({ languageError: {key: 'ui.settings.persistFailed'} })
        return
      }
      // Missing or contradictory acknowledgements require a read before any retry.
      const read = await api.settings()
      if (ticket !== languageEpoch) return
      if (read.ok && validLanguageSettings(read.data.settings)) {
        await receiveLanguage(read.data.settings)
        if (read.data.settings.language === language) return
      }
      set({ languageError: {key: 'settings.language.unconfirmed'} })
    } catch {
      try {
        const read = await api.settings()
        if (ticket === languageEpoch && read.ok && validLanguageSettings(read.data.settings)) {
          await receiveLanguage(read.data.settings)
          if (read.data.settings.language === language) return
        }
      } catch { /* The confirmed selection remains visible. */ }
      if (ticket === languageEpoch) set({ languageError: {key: 'settings.language.unconfirmed'} })
    } finally { if (ticket === languageEpoch) set({ languageSaving: false }) }
  },
  theme: readStoredTheme(),
  booted: false,
  booting: false,
  bootError: null,

  sessionCreating: false,
  sessions: [],
  sessionsLoading: false,
  activeSessionId: null,
  newSessionRevision: 0,
  newSessionCheckout: null,
  newSessionCheckoutRevision: 0,
  newSessionCheckoutStatus: 'idle',
  setNewSessionCheckout: (newSessionCheckout, newSessionCheckoutStatus = 'ready') => set(state => ({newSessionCheckout, newSessionCheckoutStatus, newSessionCheckoutRevision: state.newSessionCheckoutRevision + 1})),
  workspacePickerMode: 'add',

  workspaces: [],
  workspacesLoading: false,
  workspacesError: '',
  activeWorkspaceId: null,

  models: [],
  modelProviders: [],
  defaultModel: '',
  defaultReasoningEffort: null,
  modelVisibility: {},
  modelVisibilitySaves: {},

  status: null,
  tools: [],
  skills: [],
  workspace: null,
  sessionQuery: '',

  sidebarOpen: true,
  inspectorOpen: false,
  inspectorExpanded: false,
  layoutMode: 'columns',
  drawerIntent: null,
  // 运行时优先：模型起终端/派子代理/开作业时，用户最想知道的就是"现在在干什么"。
  inspectorTab: readInspectorTab(),
  inspectorFileTarget: null,
  inspectorRunTarget: null,
  inspectorMemberTarget: null,
  inspectorMemberTabs: {},
  inspectorMemberOrigin: null,
  inspectorJobTarget: null,
  inspectorBrowserTarget: null,
  inspectorTerminalTarget: null,
  inspectorChangesTarget: null,
  inspectorFileHistory: {},
  commandOpen: false,
  conversationSearchOpen: false,
  quickOpen: false,
  shortcutsOpen: false,
  settingsOpen: false,
  settingsTarget: null,
  settingsWorkspaceRoot: null,
  workspacePickerOpen: false,
  modelMenuOpen: false,
  reasoningSaves: {},
  defaultReasoningSave: null,
  modelSaves: {},
  nameSaves: {},
  defaultModelSave: null,
  uiFontSize: readStoredFontSize(),
  lightboxSrc: null,

  pendingQuote: null,

  toasts: [],

  boot: async () => {
    if (get().booting) return
    set({ booting: true })
    applyTheme(get().theme)
    applyFontSize(get().uiFontSize)
    try {
      // 项目与模型目录和会话列表互不依赖，并行取；任一失败都只降级不阻塞首屏。
      const sessionsTicket = ++sessionListRequest
      const workspacesTicket = ++workspaceListRequest
      const languageTicket = languageEpoch
      let catalogTicket = languageTicket
      const [initialCatalog, sessions, workspaces, initialModels, settings] = await Promise.all([
        loadCatalogs(),
        api.listSessions({retry: true}),
        api.listWorkspaces({retry: true}),
        api.models({retry: true}),
        api.settings({retry: true}),
      ])
      let catalog = initialCatalog
      let models = initialModels
      if (settings.ok && languageTicket === languageEpoch) await receiveLanguage(settings.data.settings)
      // 语言已被更新时，只重新读取目录，旧启动设置不能撤销新选择。
      while (catalogTicket !== languageEpoch) {
        catalogTicket = languageEpoch
        const [currentCatalog, currentModels] = await Promise.all([loadCatalogs(), api.models({retry: true})])
        catalog = currentCatalog
        models = currentModels
      }
      if (!catalog.status && !sessions.ok && !workspaces.ok && !models.ok && !settings.ok) {
        set({booting: false, booted: true, bootError: {key: 'ui.boot.backendUnavailable'}})
        return
      }
      const list = sessionsTicket === sessionListRequest ? sessions.ok ? sessions.data.sessions ?? [] : [] : get().sessions
      let previousSession: string | null = null
      try { previousSession = localStorage.getItem('harness.active-session.v1') } catch { /* Preferences are optional. */ }
      const activeSession = get().activeSessionId
      const selectedSession = (list.some((s) => s.id === activeSession) ? activeSession : null)
        ?? (list.some((s) => s.id === previousSession) ? previousSession : null) ?? list[0]?.id ?? null
      const wsList = workspacesTicket === workspaceListRequest && workspaces.ok ? workspaces.data.workspaces : get().workspaces
      const modelCatalog: ModelCatalog | null = models.ok ? models.data : null
      set({
        status: catalog.status,
        tools: catalog.tools,
        skills: catalog.skills,
        workspace: catalog.workspace,
        sessions: list,
        workspaces: wsList,
        workspacesError: workspacesTicket === workspaceListRequest ? workspaces.ok ? '' : workspaces.copy ?? workspaces.message : get().workspacesError,
        models: modelCatalog?.models ?? [],
        modelProviders: modelCatalog?.providers ?? [],
        defaultModel: modelCatalog?.active ?? '',
        defaultReasoningEffort: modelCatalog?.reasoning_effort ?? null,
        modelVisibility: Object.fromEntries(
          (modelCatalog?.models ?? []).map((m) => [m.id, settings.ok ? settings.data.settings.model_visibility?.[m.id] !== false : m.visible !== false]),
        ),
        booting: false,
        booted: true,
        bootError: null,
        activeSessionId: selectedSession,
        // 选中的项目要跟得上当前会话：新会话属于哪个项目就跟着切。
        activeWorkspaceId: get().activeWorkspaceId ?? workspaceOfSession(list, selectedSession, wsList) ?? null,
      })
      setFileRoot(sessionDirectory(list.find((session) => session.id === selectedSession)))
      if (!sessions.ok && sessionsTicket === sessionListRequest) {
        get().toast({
          title: {key: 'ui.store.app.235a35f9d603'},
          description: sessions.copy ?? sessions.message,
          tone: 'danger',
        })
      }
    } catch (error) {
      set({ booting: false, booted: true, bootError: failureText(error) })
      get().toast({ title: {key: 'ui.store.app.613c781999ac'}, description: failureText(error), tone: 'danger' })
    }
  },

  refreshSessions: async () => {
    const ticket = ++sessionListRequest
    set({ sessionsLoading: true })
    const result = await api.listSessions()
    if (ticket !== sessionListRequest) return false
    if (result.ok) {
      set({ sessions: result.data.sessions ?? [], sessionsLoading: false })
      const current = get()
      setFileRoot(current.activeSessionId
        ? sessionDirectory(current.sessions.find(session => session.id === current.activeSessionId))
        : current.workspaces.find(workspace => workspace.id === current.activeWorkspaceId)?.path)
    } else {
      set({ sessionsLoading: false })
      get().toast({ title: {key: 'ui.store.app.6106f0a12583'}, description: result.copy ?? result.message, tone: 'danger' })
    }
    return result.ok
  },

  /**
   * 切会话时把「项目」和「媒体根」一起同步过去。
   *
   * 媒体根必须跟着会话走而不是跟着项目走：模型写的 ``out/x.png`` 是相对
   * **它那个会话的 cwd** 的。项目跟会话不一致时，图片会去错地方找。
   */
  selectSession: (id) => {
    const state = get()
    const session = id ? state.sessions.find((s) => s.id === id) ?? null : null
    const workspaceId = workspaceOfSession(state.sessions, id)
    set({
      activeSessionId: id,
      pendingQuote: id === state.activeSessionId ? state.pendingQuote : null,
      quickOpen: id !== state.activeSessionId ? false : state.quickOpen,
      inspectorBrowserTarget: id !== state.activeSessionId ? null : state.inspectorBrowserTarget,
      activeWorkspaceId: workspaceId ?? null,
      drawerIntent: id !== state.activeSessionId ? 'none' : state.drawerIntent,
    })
    setFileRoot(sessionDirectory(session))
  },

  /**
   * 进入新对话草稿，首次发送才创建持久会话。
   *
   * 目录解析链：
   * 显式指定的项目 → 当前选中的项目 → 当前会话所属项目 → 选择项目。
   * 前端**不猜**：猜错一次，用户会以为模型在错误的目录里干了活。
   */
  startNewSession: (workspaceId) => {
    const state = get()
    const targetId = workspaceId === undefined ? state.activeWorkspaceId ?? workspaceOfSession(state.sessions, state.activeSessionId) : workspaceId
    const target = state.workspaces.find(w => w.id === targetId && w.exists !== false)
    set({ activeSessionId: null, pendingQuote: null, newSessionRevision: state.newSessionRevision + 1, activeWorkspaceId: target?.id ?? null,
      newSessionCheckout: null, newSessionCheckoutStatus: target ? 'loading' : 'idle', workspacePickerOpen: !target, workspacePickerMode: 'select', inspectorExpanded: false, drawerIntent: 'none' })
    setFileRoot(target?.path)
  },

  materializeSession: async (submission) => {
    const state = get()
    if (state.sessionCreating || state.activeSessionId) return false
    const existingRequest = useDraftStore.getState().bySession.__unsaved__?.creationRequest
    const workspace = state.workspaces.find(row => row.id === state.activeWorkspaceId)
    const checkout = state.newSessionCheckout?.cwd === workspace?.path ? state.newSessionCheckout : null
    const project = workspace && checkout ? {workspace_id: workspace.id, mode: checkout.mode,
      ...(checkout.ref ? {ref: checkout.ref} : {}), expected_head: checkout.expected_head, expected_branch: checkout.expected_branch} : undefined
    const agentSelection = useDraftStore.getState().bySession.__unsaved__?.agentSelection
    const cwd = newSessionDirectory(state.workspaces, state.activeWorkspaceId)
    const sameScope = (attempt: NonNullable<typeof existingRequest>) => attempt.cwd === cwd && attempt.agent === agentSelection?.name
      && JSON.stringify(attempt.project) === JSON.stringify(project)
    const recoveringCurrent = existingRequest && sameScope(existingRequest)
    if (!recoveringCurrent && (!workspace || workspace.exists === false)) {
      get().setWorkspacePickerOpen(true, 'select')
      get().toast({title: {key: 'ui.newConversation.chooseProject'}, tone: 'warn'})
      return false
    }
    if (!recoveringCurrent && state.newSessionCheckoutStatus !== 'ready') {
      get().toast({title: {key: 'ui.newConversation.checkBeforeSend'}, tone: 'warn'})
      return false
    }
    if (!recoveringCurrent && checkout?.mode === 'local' && checkout.ref && checkout.ref !== 'HEAD' && checkout.ref !== checkout.expected_branch
      && !window.confirm(t('ui.newConversation.confirmBranch', {current: checkout.expected_branch ?? 'HEAD', target: checkout.ref, path: checkout.cwd}))) return false
    const executionMode = useDraftStore.getState().bySession.__unsaved__?.executionMode
    set({ sessionCreating: true })
    try {
      // 首次发送等待已接受的默认强度保存，不能用仍未确认的高亮创建对话。
      const reasoningWrite = reasoningQueues.get(null)
      if (reasoningWrite) {
        await reasoningWrite.tail
        if (get().activeSessionId || get().newSessionRevision !== state.newSessionRevision || get().activeWorkspaceId !== state.activeWorkspaceId
          || get().newSessionCheckoutRevision !== state.newSessionCheckoutRevision) return false
      }
      if (defaultReasoningBlocked(null)) {
        get().toast({title: {key: 'ui.models.reasoningBeforeSend'}, tone: 'danger'})
        return false
      }
      let existing = existingRequest
      if (existing && !sameScope(existing)) {
        // 旧作用域只读确认完成后才释放身份；本次提交随后使用当前项目的新身份。
        const recovered = await recoverSessionCreation(existing.id)
        if (!recovered.ok) {
          get().toast({title: {key: 'ui.store.app.e3a3c5f92ee1'}, description: recovered.copy ?? recovered.message, tone: 'danger'})
          return false
        }
        if (useDraftStore.getState().bySession.__unsaved__?.creationRequest?.id === existing.id) useDraftStore.getState().setCreationRequest()
        sessionListRequest++
        set(current => ({sessions: current.sessions.some(row => row.id === recovered.data.session.id) ? current.sessions : [recovered.data.session, ...current.sessions]}))
        // 回查期间切换或离开草稿，不把原操作送入后来选择的目录。
        if (get().activeSessionId || get().newSessionRevision !== state.newSessionRevision || get().activeWorkspaceId !== state.activeWorkspaceId
          || get().newSessionCheckoutRevision !== state.newSessionCheckoutRevision
          || useDraftStore.getState().bySession.__unsaved__?.agentSelection !== agentSelection) return false
        existing = undefined
      }
      const attempt = existing ?? {id: crypto.randomUUID(), cwd, agent: agentSelection?.name, ...(project ? {project} : {})}
      const clearAttempt = () => {
        if (useDraftStore.getState().bySession.__unsaved__?.creationRequest?.id === attempt.id) useDraftStore.getState().setCreationRequest()
      }
      if (!existing) {
        useDraftStore.getState().setCreationRequest(attempt)
        if (!creationRetained(useDraftStore.persist.getOptions().name, attempt.id)) {
          clearAttempt()
          get().toast({title: {key: 'ui.sessionCreation.storageRequired'}, tone: 'danger'})
          return false
        }
      }
      let result: Awaited<ReturnType<typeof api.createSession>>
      try {
        result = existing ? await recoverSessionCreation(attempt.id)
          : await api.createSession({ ...(cwd ? { cwd } : {}), ...(project ? {project} : {}), ...(agentSelection ? {agent_selection: agentSelection.name} : {}) }, attempt.id)
      } catch { result = creationUnconfirmed() }
      if (result.ok && !createdFor(result.data?.session, attempt.id)) result = creationUnconfirmed()
      if (!result.ok && !existing && isUnconfirmedWrite(result)) {
        const failure = result
        const recovered = await recoverSessionCreation(attempt.id)
        result = !recovered.ok && failure.code === 'session/workspace-uncertain' ? failure : recovered
      }
      if (!result.ok) {
        if (!isUnconfirmedWrite(result)) clearAttempt()
        get().toast({ title: {key: 'ui.store.app.e3a3c5f92ee1'}, description: result.copy ?? result.message, tone: 'danger' })
        return false
      }
      if (agentSelection && (!result.data.agent_mount?.available || result.data.agent_mount.agent !== agentSelection.name)) {
        get().toast({title: {key: 'ui.composerAgents.selectionNotApplied'}, description: {key: 'ui.sessionCreation.unconfirmed'}, tone: 'danger'})
        return false
      }
      clearAttempt()
      const session = result.data.session
      if (result.data.agent_mount) useChatStore.getState().setAgentMount(session.id, result.data.agent_mount)
      const latestDraft = useDraftStore.getState().bySession.__unsaved__
      const sameDraft = get().newSessionRevision === state.newSessionRevision && get().newSessionCheckoutRevision === state.newSessionCheckoutRevision &&
        (Boolean(submission) || latestDraft?.executionMode === executionMode && latestDraft?.agentSelection === agentSelection)
      if (sameDraft && useDraftStore.getState().bySession.__unsaved__?.agentSelection === agentSelection) useDraftStore.getState().setAgentSelection('__unsaved__')
      if (executionMode) {
        useDraftStore.getState().setExecutionMode(session.id, executionMode)
        if (sameDraft && useDraftStore.getState().bySession.__unsaved__?.executionMode === executionMode) useDraftStore.getState().setExecutionMode('__unsaved__', 'normal')
      }
      if (submission) {
        useDraftStore.getState().rememberSubmission(session.id, { ...submission, sendOnReady: true })
        if (executionMode === 'plan' || executionMode === 'goal') useDraftStore.getState().consumeExecutionMode(session.id, executionMode)
      }
      sessionListRequest++
      set(current => ({ sessions: current.sessions.some(row => row.id === session.id) ? current.sessions : [session, ...current.sessions], sessionsLoading: false,
        ...(sameDraft && current.activeSessionId === null && current.newSessionRevision === state.newSessionRevision && current.activeWorkspaceId === state.activeWorkspaceId ? { activeSessionId: session.id } : {}) }))
      if (get().activeSessionId === session.id) setFileRoot(sessionDirectory(get().sessions.find(row => row.id === session.id)))
      return true
    } finally {
      set({ sessionCreating: false })
    }
  },

  refreshWorkspaces: async () => {
    const ticket = ++workspaceListRequest
    set({ workspacesLoading: true })
    const result = await api.listWorkspaces({retry: true})
    if (ticket !== workspaceListRequest) return
    set({ workspacesLoading: false })
    if (result.ok) {
      set(state => ({ workspaces: result.data.workspaces, workspacesError: '',
        ...(state.activeWorkspaceId === null && state.activeSessionId ? {activeWorkspaceId: workspaceOfSession(state.sessions, state.activeSessionId, result.data.workspaces)} : {}) }))
    } else {
      set({workspacesError: result.copy ?? result.message})
      get().toast({ title: {key: 'ui.store.app.a2de8299587b'}, description: result.copy ?? result.message, tone: 'danger' })
    }
  },

  selectWorkspace: (id) => {
    const state = get()
    if (id === state.activeWorkspaceId) return
    set({activeWorkspaceId: id, newSessionCheckout: null, newSessionCheckoutStatus: id ? 'loading' : 'idle', newSessionRevision: state.newSessionRevision + 1})
    if (!state.activeSessionId) setFileRoot(state.workspaces.find(row => row.id === id)?.path)
  },

  addWorkspace: async (path, name) => {
    const result = await api.registerWorkspace(name ? { path, name } : { path })
    if (result.ok) {
      const record = result.data.workspace
      await get().refreshWorkspaces()
      get().toast({ title: {key: 'ui.store.app.c5cd1ec0c26f'}, description: record.name, tone: 'success' })
      return record
    }
    get().toast({ title: {key: 'ui.store.app.00751ace9d67'}, description: result.copy ?? result.message, tone: 'danger' })
    return null
  },

  renameWorkspace: (id, name) => saveName('workspace', id, name),

  removeWorkspace: (id) => deleteOnce(nameSaveKey('workspace', id), async () => {
    const result = await api.unregisterWorkspace(id)
    const confirmation = await confirmDeletion(result, id, async () => {
      const read = await api.listWorkspaces()
      return read.ok && !read.data.workspaces.some(row => row.id === id)
    })
    if (confirmation === 'confirmed') {
      workspaceListRequest++
      const record = get().workspaces.find((w) => w.id === id)
      set((state) => ({
        workspaces: state.workspaces.filter((w) => w.id !== id),
        workspacesLoading: false,
        activeWorkspaceId: state.activeWorkspaceId === id ? null : state.activeWorkspaceId,
      }))
      get().toast({
        title: {key: 'ui.store.app.7666a8b4c98a'},
        description: record ? {key: "ui.store.app.d01bf10fbeee", params: {p0: record.name}} : undefined,
        tone: 'info',
      })
    } else {
      get().toast({ title: {key: confirmation === 'unconfirmed' ? 'ui.workspace.removalUnconfirmed' : 'ui.store.app.4757fc1d4709'}, description: result.ok ? {key: 'ui.deletion.receiptMismatch'} : result.copy ?? result.message, tone: 'danger' })
    }
  }),

  setSessionModel: async (sessionId, model) => {
    const key = modelSaveKey(sessionId)
    if (get().modelSaves[key]) return { ok: false, status: 409, code: 'model/busy', message: t("ui.store.app.c067c31db5ee"), copy: {key: "ui.store.app.c067c31db5ee"} }
    set((state) => ({ modelSaves: { ...state.modelSaves, [key]: true } }))
    try {
      const previous = get().sessions.find((s) => s.id === sessionId)?.model ?? null
      set((state) => ({
        sessions: state.sessions.map((s) => (s.id === sessionId ? { ...s, model: model ?? null } : s)),
      }))
      let result = await api.updateSession(sessionId, { model })
      if (result.ok && (result.data?.session?.id !== sessionId || typeof result.data.session.metadata?.model !== 'string' || (result.data.session.metadata.model || null) !== model)) {
        result = {ok: false, status: 0, code: 'model/ack-mismatch', message: t('ui.modelSave.sessionUnconfirmed'), copy: {key: 'ui.modelSave.sessionUnconfirmed'}}
      }
      if (!result.ok) {
        let actual = previous
        if (modelWriteUnconfirmed(result)) {
          const read = await api.getSession(sessionId, 'conversation-window')
          if (read.ok && read.data?.header?.id === sessionId && (read.data.header.metadata?.model == null || typeof read.data.header.metadata.model === 'string')) {
            actual = (read.data.header.metadata?.model as string | null | undefined) || null
            set((state) => ({ sessions: state.sessions.map((session) => session.id === sessionId ? { ...session, model: actual } : session) }))
            if (actual === model) return null
          }
        }
        set((state) => ({
          sessions: state.sessions.map((s) => (s.id === sessionId ? { ...s, model: actual } : s)),
        }))
        get().toast({ title: {key: modelWriteUnconfirmed(result) ? "ui.store.app.321f7045049a" : "ui.store.app.87cc7f22faaf"}, description: result.copy ?? result.message, tone: 'danger' })
        return result
      }
      return null
    } finally {
      set((state) => { const modelSaves = { ...state.modelSaves }; delete modelSaves[key]; return { modelSaves } })
    }
  },

  setReasoningEffort: async (effort, inherit = false) => {
    const id = get().activeSessionId
    if (id === null && (inherit || get().sessionCreating)) return
    let queue = reasoningQueues.get(id)
    if (!queue) {
      const session = get().sessions.find((session) => session.id === id)
      queue = { ticket: 0, confirmed: id === null ? get().defaultReasoningEffort : session?.reasoning_effort ?? null,
        confirmedOverride: session?.metadata?.reasoning_effort != null || Boolean(session?.reasoning_effort),
        uncertain: Boolean(id === null ? get().defaultReasoningSave?.unconfirmed : get().reasoningSaves[id]?.unconfirmed), tail: Promise.resolve() }
      reasoningQueues.set(id, queue)
    }
    const owner = queue
    const ticket = ++owner.ticket
    // Keep the latest intent visible, but send writes in order for this session.
    set((state) => id === null ? {
      defaultReasoningEffort: effort,
      defaultReasoningSave: { pending: true, error: null, unconfirmed: false },
    } : ({
      reasoningSaves: { ...state.reasoningSaves, [id]: { pending: true, error: null, unconfirmed: false } },
      sessions: state.sessions.map((s) =>
        s.id === id ? { ...s, reasoning_effort: inherit ? null : effort, metadata: { ...s.metadata, reasoning_effort: inherit ? null : effort ?? '' } } : s,
      ),
    }))
    owner.tail = owner.tail.then(async () => {
      let error: ResourceText | null = null
      try {
        if (id === null) {
          let result: Awaited<ReturnType<typeof api.updateSettings>>
          try { result = await api.updateSettings({ reasoning_effort: effort }) }
          catch (failure) {
            const copy = failureText(failure)
            result = {ok: false, status: 0, code: 'reasoning/unconfirmed', message: noticeText(copy), ...(typeof copy === 'object' ? {copy} : {})}
          }
          if (result.ok && (result.data?.rejected?.includes('reasoning_effort') || result.data?.settings?.reasoning_effort !== effort)) {
            result = {ok: false, status: 0, code: 'reasoning/ack-mismatch', message: t('ui.http.invalidResponse'), copy: {key: 'ui.http.invalidResponse'}}
          }
          if (result.ok) {
            owner.confirmed = effort
            owner.uncertain = false
          } else {
            error = result.copy ?? result.message
            if (modelWriteUnconfirmed(result)) {
              owner.uncertain = true
              const read = await api.settings()
              const saved = read.ok ? read.data.settings.reasoning_effort : undefined
              if (saved === null || typeof saved === 'string') {
                owner.confirmed = saved
                owner.uncertain = false
                error = saved === effort ? null : {template: {key: 'ui.store.app.340d62606aa1', params: {p0: error}}}
              }
            }
          }
        } else {
          let result = await api.updateSession(id, inherit ? { reasoning_effort_inherit: true } : { reasoning_effort: effort })
          if (result.ok && (result.data?.session?.id !== id || (inherit
            ? result.data.session.metadata?.reasoning_effort != null
            : result.data.session.metadata?.reasoning_effort !== (effort ?? '')))) {
            result = {ok: false, status: 0, code: 'reasoning/ack-mismatch', message: t('ui.http.invalidResponse'), copy: {key: 'ui.http.invalidResponse'}}
          }
          if (result.ok) {
            const saved = result.data.session.metadata?.reasoning_effort
            owner.confirmed = inherit ? null : typeof saved === 'string' ? saved || null : effort
            owner.confirmedOverride = !inherit
            owner.uncertain = false
          } else {
            error = result.copy ?? result.message
            if (modelWriteUnconfirmed(result)) {
              owner.uncertain = true
              const current = await api.getSession(id, 'conversation-window')
              if (current.ok && current.data?.header?.id === id &&
                (current.data.header.metadata?.reasoning_effort == null || typeof current.data.header.metadata.reasoning_effort === 'string')) {
                const saved = current.data.header.metadata?.reasoning_effort
                owner.confirmed = typeof saved === 'string' ? saved || null : null
                owner.confirmedOverride = current.data.header.metadata?.reasoning_effort != null
                owner.uncertain = false
                error = {template: {key: 'ui.store.app.340d62606aa1', params: {p0: result.copy ?? result.message}}}
              }
            }
          }
        }
      } catch (failure) {
        owner.uncertain = true
        error = failureText(failure)
      } finally {
        if (ticket === owner.ticket) {
          set((state) => id === null ? {
            defaultReasoningSave: { pending: false, error, unconfirmed: owner.uncertain },
            ...(owner.uncertain ? {} : { defaultReasoningEffort: owner.confirmed }),
          } : ({
            reasoningSaves: { ...state.reasoningSaves, [id]: { pending: false, error, unconfirmed: owner.uncertain } },
            sessions: owner.uncertain ? state.sessions : state.sessions.map((session) => {
              if (session.id !== id) return session
              const metadata = { ...session.metadata }
              if (owner.confirmedOverride) metadata.reasoning_effort = owner.confirmed ?? ''
              else delete metadata.reasoning_effort
              return { ...session, metadata, reasoning_effort: owner.confirmed }
            }),
          }))
          reasoningQueues.delete(id)
        }
      }
    })
    await owner.tail
  },

  setDefaultModel: async (model) => {
    const key = modelSaveKey(null)
    if (get().modelSaves[key]) return { ok: false, status: 409, code: 'model/busy', message: t("ui.store.app.1504ffb63d4d"), copy: {key: "ui.store.app.1504ffb63d4d"} }
    set((state) => ({ modelSaves: { ...state.modelSaves, [key]: true }, defaultModelSave: { target: model, error: null, unconfirmed: false } }))
    try {
      const previous = get().defaultModel
      let result = await api.updateSettings({ default_model: model })
      if (result.ok && (result.data.rejected?.includes('default_model') || result.data.settings?.default_model !== model)) {
        result = { ok: false, status: 0, code: 'model/ack-mismatch', message: t("ui.store.app.c2c3ae25a3d5"), copy: {key: "ui.store.app.c2c3ae25a3d5"} }
      }
      if (!result.ok) {
        const unconfirmed = modelWriteUnconfirmed(result)
        if (unconfirmed) {
          const read = await api.models()
          if (read.ok) {
            set({ defaultModel: read.data.active, models: read.data.models })
            if (read.data.active === model) { set({ defaultModelSave: null }); return null }
            set({ defaultModelSave: { target: model, error: result.copy ?? result.message, unconfirmed: true } })
            get().toast({ title: {key: "ui.store.app.d5c1c9461536"}, description: result.copy ?? result.message, tone: 'danger' })
            return result
          }
        }
        set({ defaultModel: previous })
        set({ defaultModelSave: { target: model, error: result.copy ?? result.message, unconfirmed } })
        get().toast({ title: {key: unconfirmed ? "ui.store.app.d5c1c9461536" : "ui.store.app.7d58f03b954d"}, description: result.copy ?? result.message, tone: 'danger' })
        return result
      }
      // 目录里的 active 标记要跟着走，否则「当前模型」那一行会指着旧的。
      set((state) => ({
        defaultModel: model,
        defaultModelSave: null,
        models: state.models.map((m) => ({ ...m, active: m.id === model, visible: m.id === model || state.modelVisibility[m.id] !== false })),
      }))
      return null
    } catch (error) {
      const text = failureText(error)
      const failure: ApiFailure = { ok: false, status: 0, code: 'model/unconfirmed', message: noticeText(text), ...(typeof text === 'object' ? {copy: text} : {}) }
      set({ defaultModelSave: { target: model, error: failure.copy ?? failure.message, unconfirmed: true } })
      get().toast({ title: {key: "ui.store.app.d5c1c9461536"}, description: failure.copy ?? failure.message, tone: 'danger' })
      return failure
    } finally {
      set((state) => { const modelSaves = { ...state.modelSaves }; delete modelSaves[key]; return { modelSaves } })
    }
  },

  dismissDefaultModelFeedback: () => {
    if (!get().modelSaves[modelSaveKey(null)]) set({ defaultModelSave: null })
  },

  setModelVisibility: async (modelId, visible) => {
    if (get().modelVisibilitySaves[modelId]?.pending) return
    let confirmed = get().modelVisibility[modelId] !== false
    let error: ResourceText | null = null
    let unconfirmed = false
    set((state) => ({ modelVisibilitySaves: { ...state.modelVisibilitySaves, [modelId]: { pending: true, target: visible, error: null, unconfirmed: false } } }))
    try {
      let result = await api.updateSettings({ model_visibility: { [modelId]: visible } })
      if (result.ok && (result.data.rejected?.includes('model_visibility') || result.data.settings?.model_visibility?.[modelId] !== visible)) {
        result = { ok: false, status: 0, code: 'visibility/ack-mismatch', message: t("ui.store.app.3a29146bc215"), copy: {key: "ui.store.app.3a29146bc215"} }
      }
      if (result.ok) confirmed = visible
      else {
        error = result.copy ?? result.message
        unconfirmed = modelWriteUnconfirmed(result)
        if (unconfirmed) {
          const read = await api.settings()
          if (read.ok) {
            confirmed = read.data.settings.model_visibility?.[modelId] !== false
            unconfirmed = false
            if (confirmed === visible) error = null
          }
        }
      }
    } catch (failure) {
      error = failureText(failure)
      unconfirmed = true
    } finally {
      set((state) => ({
        modelVisibility: { ...state.modelVisibility, [modelId]: confirmed },
        models: state.models.map((model) => model.id === modelId ? { ...model, visible: model.id === state.defaultModel || confirmed } : model),
        modelVisibilitySaves: { ...state.modelVisibilitySaves, [modelId]: { pending: false, target: visible, error, unconfirmed } },
      }))
    }
    if (error) get().toast({ title: {key: unconfirmed ? "ui.store.app.c49ec814425c" : "ui.store.app.b9e0cc2cb862"}, description: error, tone: 'danger' })
  },

  removeSession: async (id) => {
    const originalDrafts = captureSessionLocalDrafts(id)
    return deleteOnce(nameSaveKey('session', id), async () => {
      const result = await api.deleteSession(id)
      const confirmation = await confirmDeletion(result, id, async () => {
        const read = await api.listSessions()
        return read.ok && !read.data.sessions.some(row => row.id === id)
      })
      if (confirmation === 'confirmed') {
        sessionListRequest++
        const retained = !sameSessionLocalDrafts(originalDrafts, captureSessionLocalDrafts(id))
        if (!retained) {
          useDraftStore.getState().discard(id)
          useGitDraftStore.getState().discardSession(id)
        }
        useChatStore.getState().drop(id)
        set((state) => {
          const sessions = state.sessions.filter((s) => s.id !== id)
          const memberTabs = Object.fromEntries(Object.entries(state.inspectorMemberTabs).filter(([, entry]) => entry.target.owner !== id))
          const fileHistory = { ...state.inspectorFileHistory }
          delete fileHistory[filePreviewScope(id)]
          const closingMember = state.inspectorMemberTabs[state.inspectorTab]?.target.owner === id
          return {
            sessions,
            sessionsLoading: false,
            inspectorMemberTabs: memberTabs,
            inspectorFileHistory: fileHistory,
            inspectorTab: closingMember ? 'home' as const : state.inspectorTab,
            inspectorMemberTarget: state.inspectorMemberTarget?.owner === id ? null : state.inspectorMemberTarget,
            inspectorMemberOrigin: state.inspectorMemberTarget?.owner === id ? null : state.inspectorMemberOrigin,
            inspectorFileTarget: state.inspectorFileTarget?.sessionId === id ? null : state.inspectorFileTarget,
            inspectorRunTarget: state.inspectorRunTarget?.sessionId === id ? null : state.inspectorRunTarget,
            inspectorJobTarget: state.inspectorJobTarget?.sessionId === id ? null : state.inspectorJobTarget,
            inspectorBrowserTarget: state.inspectorBrowserTarget?.sessionId === id ? null : state.inspectorBrowserTarget,
            inspectorTerminalTarget: state.inspectorTerminalTarget?.sessionId === id ? null : state.inspectorTerminalTarget,
            inspectorChangesTarget: state.inspectorChangesTarget?.sessionId === id ? null : state.inspectorChangesTarget,
          }
        })
        // 删除回执可能晚于用户切换；只按回执到达时的实际会话同步目录。
        const current = get()
        if (current.activeSessionId === id) get().selectSession(current.sessions[0]?.id ?? null)
        get().toast({ title: {key: 'ui.store.app.495e7952925d'}, tone: retained ? 'warn' : 'info', ...(retained ? {
          persistent: true,
          description: {key: 'ui.session.retainedLocalDrafts'},
          action: {label: {key: 'ui.session.retainedLocalDownload'}, run: () => download(
            'conversation-local-content-' + id + '.json', JSON.stringify({sessionId: id, ...captureSessionLocalDrafts(id)}, null, 2), 'application/json')},
        } : {}) })
      } else {
        get().toast({ title: {key: confirmation === 'unconfirmed' ? 'ui.session.deletionUnconfirmed' : 'ui.store.app.c228558cf257'}, description: result.ok ? {key: 'ui.deletion.receiptMismatch'} : result.copy ?? result.message, tone: 'danger' })
      }
    })
  },

  renameSession: (id, title) => saveName('session', id, title),

  renameLocal: (id, title) =>
    set((state) => ({
      sessions: state.sessions.map((s) => (s.id === id ? { ...s, title } : s)),
    })),

  setSessionQuery: (sessionQuery) => set({ sessionQuery }),

  setTheme: (theme) => {
    applyTheme(theme)
    set({ theme })
  },

  // 图标按钮只做一件事：在**当前实际显示的**明暗之间来回切。
  //
  // 之前这里是 light -> dark -> system -> light 的三态轮转，起点是
  // 'system'（默认值）。于是新用户第一次点这个按钮得到的是 'light' ——
  // 如果系统本来就是浅色，界面纹丝不动，按钮看起来"坏了"。
  //
  // 现在按 resolveTheme() 的**实际结果**取反：看到浅色就点出深色，
  // 看到深色就点出浅色，和太阳/月亮图标的直觉一致。"跟随系统"是偏好，
  // 属于设置面板里的选项，不该藏在一个看图形的切换按钮后面。
  toggleTheme: () =>
    get().setTheme(resolveTheme(get().theme) === 'dark' ? 'light' : 'dark'),

  toggleSidebar: () => {
    if (get().layoutMode === 'overlay') get().toggleDrawer('left')
    else set((state) => ({ sidebarOpen: !state.sidebarOpen, drawerIntent: 'none' }))
  },
  toggleInspector: () => {
    if (get().inspectorExpanded) {
      set({ inspectorExpanded: false, inspectorOpen: false, drawerIntent: 'none' })
      return
    }
    if (get().layoutMode === 'overlay') get().toggleDrawer('right')
    else set((state) => ({ inspectorOpen: !state.inspectorOpen, drawerIntent: 'none' }))
  },
  toggleInspectorExpanded: () => set((state) => ({
    inspectorExpanded: !state.inspectorExpanded,
    inspectorOpen: true,
    drawerIntent: 'right',
  })),
  toggleDrawer: (which) =>
    set((state) => ({
      drawerIntent: state.drawerIntent === which ? 'none' : which,
    })),
  closeDrawer: () => set({ drawerIntent: 'none' }),
  setLayoutMode: (layoutMode) => {
    if (get().layoutMode !== layoutMode) set({ layoutMode })
  },
  setInspectorTab: (inspectorTab) => {
    const member = get().inspectorMemberTabs[inspectorTab]
    if (inspectorTab.startsWith('member:') && member?.target.owner !== get().activeSessionId) return
    set({ inspectorOpen: true, inspectorTab, drawerIntent: 'right', inspectorMemberTarget: member?.target ?? null, inspectorMemberOrigin: member?.origin ?? null })
  },
  revealRun: (sessionId, target) => set({ inspectorOpen: true, inspectorTab: 'queue', drawerIntent: 'right', inspectorMemberTarget: null, inspectorMemberOrigin: null,
    inspectorRunTarget: { ...target, sessionId, seq: ++revealSeq } }),
  dismissRunPreview: () => set({ inspectorRunTarget: null }),
  revealMember: (target, origin) => {
    const tab: InspectorTab = `member:${JSON.stringify([target.owner, target.id])}`
    const memberOrigin = origin ?? (typeof document === 'undefined' ? null : document.activeElement as HTMLElement | null)
    set(state => ({ inspectorOpen: true, inspectorTab: tab, drawerIntent: 'right', inspectorMemberTarget: target, inspectorMemberOrigin: memberOrigin,
      inspectorMemberTabs: { ...state.inspectorMemberTabs, [tab]: { target, origin: memberOrigin } } }))
  },
  closeMemberTab: (tab) => {
    const entry = get().inspectorMemberTabs[tab]
    if (!entry) return
    const tabs = { ...get().inspectorMemberTabs }
    delete tabs[tab]
    set({ inspectorMemberTabs: tabs })
    if (get().inspectorTab !== tab) return
    get().setInspectorTab('home')
    const origin = entry.origin
    if (origin && typeof requestAnimationFrame === 'function') requestAnimationFrame(() => {
      const state = get()
      if (state.inspectorTab === 'home' && !state.commandOpen && !state.settingsOpen && !state.shortcutsOpen && !state.conversationSearchOpen && !state.quickOpen && !state.lightboxSrc && origin.isConnected && !origin.closest('[inert]')) origin.focus({ preventScroll: true })
    })
  },
  dismissMember: () => get().closeMemberTab(get().inspectorTab),
  revealJob: (sessionId, jobId) => set({ inspectorOpen: true, inspectorTab: 'runtime', drawerIntent: 'right', inspectorMemberTarget: null, inspectorMemberOrigin: null,
    inspectorJobTarget: { sessionId, jobId, seq: ++revealSeq } }),
  revealBrowser: (sessionId, url) => set({ inspectorOpen: true, inspectorTab: 'browser', drawerIntent: 'right', inspectorMemberTarget: null, inspectorMemberOrigin: null,
    inspectorBrowserTarget: { sessionId, url, seq: ++revealSeq } }),
  revealTerminal: (sessionId, terminalId) => set({ inspectorOpen: true, inspectorTab: 'terminal', drawerIntent: 'right', inspectorMemberTarget: null, inspectorMemberOrigin: null,
    inspectorTerminalTarget: { sessionId, terminalId, seq: ++revealSeq } }),
  revealTurnChanges: (sessionId, turn, path) => set({ inspectorOpen: true, inspectorTab: 'changes', drawerIntent: 'right', inspectorMemberTarget: null, inspectorMemberOrigin: null,
    inspectorChangesTarget: { sessionId, turn, seq: ++revealSeq, path } }),
  dismissTurnChanges: () => set({ inspectorChangesTarget: null }),
  revealInFiles: (path, lineRange, source) =>
    set((state) => ({
      inspectorOpen: true,
      inspectorTab: 'files',
      inspectorMemberTarget: null,
      inspectorMemberOrigin: null,
      drawerIntent: 'right',
      inspectorFileTarget: { path, seq: (revealSeq += 1), sessionId: state.activeSessionId, root: source ? source.root : getFileRoot(), directory: source?.directory, focusContent: source?.focusContent, lineRange },
      inspectorFileHistory: source?.directory ? state.inspectorFileHistory : rememberFilePreview(state.inspectorFileHistory, state.activeSessionId, { path, root: source ? source.root : getFileRoot(), lineRange }),
    })),
  dismissFilePreview: () => set({ inspectorFileTarget: null }),
  closeFileTab: (reference) => set((state) => {
    const scope = filePreviewScope(state.activeSessionId)
    const { entries, next } = closeFilePreview(state.inspectorFileHistory[scope] ?? [], reference)
    const target = state.inspectorFileTarget
    const active = target && target.sessionId === state.activeSessionId && !target.directory
      && sameFileReference({ ...target, root: target.root ?? null }, reference)
    return {
      inspectorFileHistory: { ...state.inspectorFileHistory, [scope]: entries },
      inspectorFileTarget: active ? next ? { ...next, sessionId: state.activeSessionId, seq: ++revealSeq, focusContent: false } : null : target,
    }
  }),
  setCommandOpen: (commandOpen) => set({ commandOpen }),
  setConversationSearchOpen: (conversationSearchOpen) => set({ conversationSearchOpen }),
  setQuickOpen: (quickOpen) => set({ quickOpen: quickOpen && Boolean(get().activeSessionId) }),
  setShortcutsOpen: (shortcutsOpen) => set({ shortcutsOpen }),
  setSettingsOpen: (settingsOpen, target, root) => {
    const closing = get().settingsOpen && !settingsOpen
    const ticket = ++settingsStatusRequest
    set({ settingsOpen, settingsTarget: target ?? null, settingsWorkspaceRoot: root ?? null })
    if (closing) {
      // 设置可能改变连接或订阅；关闭后重新读取，不沿用启动时的凭据状态。
      set({status: null})
      void api.status().then(result => {
        if (ticket === settingsStatusRequest && !get().settingsOpen) set({status: result.ok ? result.data : null})
      })
    }
  },
  setWorkspacePickerOpen: (workspacePickerOpen, workspacePickerMode = 'add') => set({ workspacePickerOpen, workspacePickerMode }),
  setModelMenuOpen: (modelMenuOpen) => set({ modelMenuOpen }),
  setUiFontSize: (uiFontSize) => {
    set({ uiFontSize: applyFontSize(uiFontSize) })
  },

  openLightbox: (src, alt) => set({ lightboxSrc: { src, alt } }),
  openImageGallery: (images, index = 0) => {
    if (!images.length) return
    const selected = Math.max(0, Math.min(images.length - 1, index))
    set({ lightboxSrc: { ...images[selected]!, images, index: selected } })
  },
  stepLightbox: (delta) => set((state) => {
    const value = state.lightboxSrc
    if (!value?.images?.length) return {}
    const index = Math.max(0, Math.min(value.images.length - 1, (value.index ?? 0) + delta))
    return { lightboxSrc: { ...value.images[index]!, images: value.images, index } }
  }),
  closeLightbox: () => set({ lightboxSrc: null }),

  quoteInto: (sessionId, text) => {
    if (get().activeSessionId !== sessionId) return
    quoteSeq += 1
    // A quote from the mobile inspector hands control back to the composer.
    // Its existing focus effect runs after the drawer releases inert content.
    set({ pendingQuote: { seq: quoteSeq, sessionId, text }, ...(get().layoutMode === 'overlay' ? { drawerIntent: 'none' as const } : {}) })
  },
  clearQuote: (seq) => set(state => state.pendingQuote?.seq === seq ? { pendingQuote: null } : {}),

  toast: (toast) => {
    toastSeq += 1
    const id = 't' + toastSeq
    set((state) => ({ toasts: [...state.toasts, { ...toast, id, duration: toastDuration(toast) }] }))
    return id
  },

  dismissToast: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),

}))

/** 服务恢复、回到页面及网络上线共用准入，只补读失败目录，不并发重读。 */
export function recoverProjectCatalog() {
  const state = useAppStore.getState()
  if (state.booting || state.workspacesLoading || !state.workspacesError || typeof document !== 'undefined' && document.hidden) return
  return state.refreshWorkspaces()
}

useAppStore.subscribe((state, previous) => {
  if (state.inspectorTab !== previous.inspectorTab) {
    try { localStorage.setItem('harness.inspector-tab.v1', state.inspectorTab) }
    catch { /* Navigation still works without preference storage. */ }
  }
  if (state.activeSessionId === previous.activeSessionId) return
  try {
    if (state.activeSessionId) localStorage.setItem('harness.active-session.v1', state.activeSessionId)
    else localStorage.removeItem('harness.active-session.v1')
  } catch { /* A blocked preference store must not prevent navigation. */ }
})

/** 会话所属项目 id；cwd 没命中任何注册项目则返回 null。 */
function workspaceOfSession(
  sessions: SessionSummary[],
  sessionId: string | null,
  workspaces: WorkspaceInfo[] = useAppStore.getState().workspaces,
): string | null {
  const session = sessionId ? sessions.find((s) => s.id === sessionId) : null
  if (!session) return null
  return matchWorkspaceFor(sessionProjectDirectory(session), workspaces)?.id ?? null
}

/** 当前会话的摘要（可能尚未进入列表）。 */
export function useActiveSession(): SessionSummary | null {
  return useAppStore((state) => {
    if (state.activeSessionId === null) return null
    return state.sessions.find((s) => s.id === state.activeSessionId) ?? null
  })
}
