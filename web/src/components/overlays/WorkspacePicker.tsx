import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowUp,
  Check,
  ChevronRight,
  Folder,
  FolderPlus,
  HardDrive,
  Home,
  Monitor,
  Loader2,
  Search,
  X,
} from 'lucide-react'
import { api } from '@/lib/api'
import { normalizePath } from '@/lib/workspaces'
import { useAppStore } from '@/store/app'
import { Button, Kbd, Overlay, RefreshButton } from '@/components/ui/primitives'
import type { DirectoryListing } from '@/types'
import {ConversationCheckout} from '@/components/chat/ConversationCheckout'
import type {ConversationCheckout as Checkout} from '@/lib/conversationProject'

/**
 * 项目目录选择器。
 *
 * 交互参考 ui-directory-picker-browse，但补上了人类操作里真正缺的两块：
 *   - **面包屑可点**：目录深了以后，"回到上一级"只有一个 ↑ 键是不够的；
 *   - **就地新建文件夹**：选到一个还不存在的目录时，与其让用户去资源管理器
 *     建好再回来，不如在原地建完直接选中。
 *
 * 列表只列子目录（后端 ``/api/v1/directories``），所以每一层都是一个明确的动作：
 * 「进入」或「就用这个」。
 */

type Status = 'idle' | 'loading' | 'error'

export function WorkspacePicker() {
  useLocale()

  const open = useAppStore((s) => s.workspacePickerOpen)
  const mode = useAppStore(s => s.workspacePickerMode)
  const setOpen = useAppStore((s) => s.setWorkspacePickerOpen)
  const addWorkspace = useAppStore((s) => s.addWorkspace)
  const startNewSession = useAppStore((s) => s.startNewSession)
  const toast = useAppStore((s) => s.toast)
  const workspaces = useAppStore((s) => s.workspaces)

  const [listing, setListing] = useState<DirectoryListing | null>(null)
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState<ResourceText>('')
  const [query, setQuery] = useState('')
  const [pathInput, setPathInput] = useState('')
  const [making, setMaking] = useState(false)
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState<string>('')
  const [checkout, setCheckout] = useState<Checkout | null>(null)
  const [checkoutStatus, setCheckoutStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const updateCheckout = useCallback((value: Checkout | null, status: 'loading' | 'ready' | 'error') => {
    setCheckout(value); setCheckoutStatus(status)
  }, [])
  const reqSeq = useRef(0)
  const directoryRequest = useRef<AbortController | null>(null)
  const requestedPath = useRef<string | undefined>(undefined)
  const panelEpoch = useRef(0)
  const folderPending = useRef(false)
  const registrationPending = useRef(false)

  const load = useCallback(async (path?: string) => {
    if (registrationPending.current || folderPending.current) return
    requestedPath.current = path
    const seq = ++reqSeq.current
    directoryRequest.current?.abort()
    const controller = new AbortController()
    directoryRequest.current = controller
    setStatus('loading')
    setError('')
    setSelected('')
    setCheckout(null); setCheckoutStatus('loading')
    setMaking(false)
    const result = await api.listDirectories(path, controller.signal)
    // 快速连点时后到的旧响应必须丢掉，否则目录会"跳回"上一层。
    if (seq !== reqSeq.current || controller.signal.aborted) return
    if (result.ok) {
      setListing(result.data)
      setSelected(result.data.path)
      setPathInput(result.data.path)
      setQuery('')
      setStatus('idle')
    } else {
      setStatus('error')
      setError(result.copy ?? result.message)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    panelEpoch.current++
    setQuery('')
    setMaking(false)
    setNewName('')
    const state = useAppStore.getState()
    void state.refreshWorkspaces()
    void load(mode === 'select' ? state.workspaces.find(row => row.id === state.activeWorkspaceId)?.path : undefined)
    return () => {
      panelEpoch.current++
      reqSeq.current++
      directoryRequest.current?.abort()
      directoryRequest.current = null
    }
  }, [open, mode, load])

  const enter = useCallback(
    (path: string) => {
      void load(path)
    },
    [load],
  )

  const goUp = useCallback(() => {
    if (listing?.parent) void load(listing.parent)
    else if (listing?.path && listing.roots?.length) void load('::computer')
  }, [listing, load])

  const computer = listing?.path === ''
  const crumbs = useMemo(() => {
    const path = listing?.path || ''
    // 从真实路径生成层级，直接跳转和返回上级后也保持准确；UNC 根作为整体。
    const root = path.match(/^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+\\?|\/)/)?.[0]
    if (!root) return []
    const separator = path.includes('\\') ? '\\' : '/'
    const parts = path.slice(root.length).split(/[\\/]/).filter(Boolean)
    return [{ name: root, path: root }, ...parts.map((name, index) => ({
      name, path: root.replace(/[\\/]$/, '') + separator + parts.slice(0, index + 1).join(separator),
    }))]
  }, [listing?.path])

  const entries = useMemo(() => {
    const all = listing?.entries ?? []
    const q = query.trim().toLowerCase()
    const filtered = q ? all.filter((e) => e.name.toLowerCase().includes(q)) : all
    // 命中搜索时不再按字母排 —— 用户已经用类型表达了顺序意图，
    // 再排序等于跟他打架。
    return q ? filtered : all
  }, [listing, query])

  const already = useMemo(() => {
    const set = new Set(workspaces.map((w) => normalizePath(w.path)))
    return (path: string) => set.has(normalizePath(path))
  }, [workspaces])

  const commit = useCallback(
    async (path: string, withSession: boolean) => {
      if (registrationPending.current || folderPending.current) return
      const epoch = panelEpoch.current
      const origin = useAppStore.getState()
      const current = () => {
        const latest = useAppStore.getState()
        return epoch === panelEpoch.current && latest.workspacePickerOpen &&
          latest.activeSessionId === origin.activeSessionId && latest.newSessionRevision === origin.newSessionRevision &&
          latest.activeWorkspaceId === origin.activeWorkspaceId
      }
      registrationPending.current = true
      setBusy(true)
      const existing = workspaces.find(row => normalizePath(row.path) === normalizePath(path))
      const record = await (existing ? Promise.resolve(existing) : addWorkspace(path)).finally(() => {
        registrationPending.current = false
        setBusy(false)
      })
      if (!record || !current()) return
      useAppStore.getState().selectWorkspace(record.id)
      setOpen(false)
      if (withSession || mode === 'select') {
        startNewSession(record.id)
        if (checkout?.cwd === path) useAppStore.getState().setNewSessionCheckout({...checkout, cwd: record.path})
      }
    },
    [addWorkspace, startNewSession, setOpen, workspaces, checkout, mode],
  )

  const createFolder = useCallback(async () => {
    const name = newName.trim()
    if (!name || !listing?.path || status !== 'idle' || folderPending.current || registrationPending.current) return
    const epoch = panelEpoch.current
    folderPending.current = true
    setBusy(true)
    const result = await api.mkdirWorkspace(listing.path, name).finally(() => {
      folderPending.current = false
      setBusy(false)
    })
    if (epoch !== panelEpoch.current) return
    if (!result.ok) {
      toast({ title: {key: "ui.components.overlays.WorkspacePicker.51ccf18f43ba"}, description: result.copy ?? result.message, tone: 'danger' })
      return
    }
    setMaking(false)
    setNewName('')
    enter(result.data.created)
  }, [enter, listing, newName, toast, status])

  if (!open) return null

  return (
    <Overlay onClose={() => setOpen(false)} className="flex max-h-dialog max-w-2xl flex-col overflow-y-auto sm:max-h-dialog-wide" labelledBy="picker-title">
      <header className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-3">
        <Folder className="h-4 w-4 text-brand" />
        <h2 id="picker-title" className="ui-section-title">{t(mode === 'select' ? 'ui.newConversation.chooseProject' : 'ui.components.overlays.WorkspacePicker.8964f53ac30f')}</h2>
        <span className="truncate text-2xs text-faint">{t("ui.components.overlays.WorkspacePicker.0294c1647bf9")}</span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="ml-auto rounded-lg p-1.5 text-faint transition-colors hover:bg-raised hover:text-ink"
          aria-label={t("ui.components.overlays.WorkspacePicker.3fd47edce45b")}
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
      {workspaces.length > 0 && <section className="space-y-2 border-b border-line px-4 py-3" aria-label={t('ui.newConversation.registeredProjects')}>
        <h3 className="ui-group-title text-muted">{t('ui.newConversation.registeredProjects')}</h3>
        <div className="flex max-h-32 flex-wrap gap-2 overflow-y-auto">
          {workspaces.map(workspace => <Button key={workspace.id} size="sm" disabled={busy || workspace.exists === false} active={normalizePath(selected) === normalizePath(workspace.path)}
            title={workspace.path} icon={<Folder className="h-3.5 w-3.5" />} onClick={() => enter(workspace.path)}>
            <span className="max-w-path truncate">{workspace.name}</span>
            {workspace.exists === false ? <span className="text-warn">{t('ui.workspacePicker.missingDirectory')}</span> : null}
          </Button>)}
        </div>
      </section>}

      {/* 路径条：面包屑 + 上一级 + 手动输入，三条路都能到任意目录 */}
      <div className="refresh-action-container flex items-center gap-1.5 border-b border-line px-3 py-2">
        <button
          type="button"
          onClick={goUp}
          disabled={status === 'loading' || (!listing?.parent && !(listing?.path && listing.roots?.length))}
          className="rounded-md border border-line p-1.5 text-muted transition-colors hover:text-ink disabled:opacity-35"
          title={t("ui.components.overlays.WorkspacePicker.8e7847be62b6")}
          aria-label={t("ui.components.overlays.WorkspacePicker.8e7847be62b6")}
        >
          <ArrowUp className="h-3.5 w-3.5" />
        </button>
        <button type="button" onClick={() => void load()}
          className="rounded-md border border-line p-1.5 text-muted transition-colors hover:text-brand"
          title={t('ui.components.overlays.WorkspacePicker.6bff6a717a8c')}
          aria-label={t('ui.components.overlays.WorkspacePicker.6bff6a717a8c')}>
          <Home className="h-3.5 w-3.5" />
        </button>
        <nav className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto no-scrollbar">
          {listing?.roots?.length ? (
            <button type="button" onClick={() => void load('::computer')}
              className="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-2xs text-muted hover:text-brand">
              <Monitor className="h-3.5 w-3.5" />{t('ui.workspacePicker.computer')}
            </button>
          ) : null}
          {crumbs.map((crumb) => (
            <span key={crumb.path} className="flex shrink-0 items-center">
              <ChevronRight className="h-3 w-3 text-faint" />
              <button
                type="button"
                onClick={() => void load(crumb.path)}
                className="max-w-path truncate rounded px-1.5 py-0.5 text-2xs text-muted transition-colors hover:text-brand"
              >
                {crumb.name}
              </button>
            </span>
          ))}
        </nav>
        <RefreshButton size="xs" label={t("ui.components.overlays.WorkspacePicker.aee887434131")}
          spinning={status === 'loading'} onClick={() => void load(computer ? '::computer' : listing?.path)} />
      </div>

      {/* 完整路径可编辑：目录名带空格/中文时，点面包屑不如直接粘一条绝对路径 */}
      <form className="flex items-center gap-2 border-b border-line px-3 py-2" onSubmit={(event) => {
        event.preventDefault()
        if (pathInput.trim()) void load(pathInput.trim())
      }}>
        <input
          value={pathInput}
          onKeyDown={event => {
            if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()
          }}
          onChange={(event) => setPathInput(event.target.value)}
          placeholder={t('ui.workspacePicker.pathPlaceholder')}
          aria-label={t('ui.workspacePicker.path')}
          className="min-w-0 flex-1 rounded-lg border border-line bg-raised px-2.5 py-1.5 text-xs text-ink placeholder:text-faint focus:border-brand/50 focus:outline-none"
        />
        <Button size="xs" variant="secondary" disabled={!pathInput.trim() || status === 'loading'} onClick={() => void load(pathInput.trim())}>{t('ui.workspacePicker.go')}</Button>
      </form>
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <Search className="h-3.5 w-3.5 text-faint" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t('ui.workspacePicker.filter')}
          className="min-w-0 flex-1 rounded-lg border border-line bg-raised px-2.5 py-1.5 text-xs text-ink placeholder:text-faint focus:border-brand/50 focus:outline-none"
          aria-label={t('ui.workspacePicker.filter')}
        />
        {query ? (
          <Button size="xs" variant="ghost" onClick={() => setQuery('')}>{t("ui.components.overlays.WorkspacePicker.bce2377283c2")}</Button>
        ) : null}
      </div>

      <div className="min-h-32 p-2">
        {status === 'error' ? (
          <div className="space-y-3 px-3 py-6 text-center"><p role="alert" className="text-xs text-danger">{noticeText(error)}</p><Button size="sm" variant="secondary" onClick={() => void load(requestedPath.current)}>{t("ui.components.chat.Conversation.6d7f1def5670")}</Button></div>
        ) : status === 'loading' ? (
          <div className="flex items-center justify-center gap-2 py-10 text-xs text-faint">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />{t("ui.components.overlays.WorkspacePicker.836ebad63923")}</div>
        ) : entries.length === 0 ? (
          <p className="px-3 py-10 text-center text-xs text-faint">
            {query ? t("ui.components.overlays.WorkspacePicker.6a512257e010") : t("ui.components.overlays.WorkspacePicker.c2d72b7fe941")}
          </p>
        ) : (
          entries.map((entry) => {
            const added = already(entry.path)
            return (
              <div
                key={entry.path}
                className="group flex items-center gap-1 rounded-lg px-1 transition-colors hover:bg-raised/70"
              >
                <button
                  type="button"
                  onClick={() => enter(entry.path)}
                  className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1.5 text-left"
                >
                  {computer ? <HardDrive className="h-4 w-4 shrink-0 text-brand/70" /> : <Folder className="h-3.5 w-3.5 shrink-0 text-brand/70" />}
                  <span className="min-w-0 flex-1 truncate text-xs text-ink">{entry.name}</span>
                  {added ? (
                    <span className="shrink-0 text-2xs text-faint">{t("ui.components.overlays.WorkspacePicker.889839915c3f")}</span>
                  ) : null}
                </button>
                {mode !== 'select' && <button
                  type="button"
                  onClick={() => void commit(entry.path, false)}
                  disabled={added || busy || status !== 'idle'}
                  className="shrink-0 rounded-md px-2 py-1 text-2xs text-muted opacity-0 transition-opacity hover:bg-brand/[0.12] hover:text-brand disabled:opacity-40 group-hover:opacity-100"
                >{t("ui.components.overlays.WorkspacePicker.7a8a11ead507")}</button>}
              </div>
            )
          })
        )}

        {listing?.truncated ? <p className="px-3 py-2 text-2xs text-faint">{t('ui.workspacePicker.truncated')}</p> : null}
        {making ? (
          <div className="mt-2 flex items-center gap-2 rounded-lg border border-brand/30 bg-brand/6 px-2 py-2">
            <FolderPlus className="h-3.5 w-3.5 shrink-0 text-brand" />
            <input
              autoFocus
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              onKeyDown={(event) => {
                if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
                if (event.key === 'Enter') void createFolder()
                if (event.key === 'Escape') setMaking(false)
              }}
              placeholder={t("ui.components.overlays.WorkspacePicker.ae51764fbb38")}
              className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2 py-1 text-xs text-ink placeholder:text-faint focus:border-brand/50 focus:outline-none"
            />
            <Button size="xs" variant="primary" disabled={!newName.trim() || busy} onClick={() => void createFolder()}>{t("ui.components.overlays.WorkspacePicker.cde2cd071d25")}</Button>
          </div>
        ) : null}
      </div>

      {selected && status === 'idle' && <div className="border-t border-line px-4 py-3">
        <ConversationCheckout cwd={selected} value={checkout} disabled={busy} onChange={updateCheckout} />
      </div>}
      <p className="border-t border-line px-4 py-2 text-2xs text-faint">
        <Search className="mr-1 inline h-3 w-3" />{t("ui.components.overlays.WorkspacePicker.be8e4dd716fb")}<Kbd>Enter</Kbd>{t("ui.components.overlays.WorkspacePicker.b752f04f6a55")}<Kbd>Esc</Kbd>{t("ui.components.overlays.WorkspacePicker.3fd47edce45b")}</p>
      </div>
      <footer className="flex shrink-0 flex-wrap items-center gap-2 border-t border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs text-ink" title={selected}>
            {computer ? t('ui.workspacePicker.computer') : selected || '—'}
          </p>
          <p className="mt-0.5 text-2xs text-faint">{t("ui.components.overlays.WorkspacePicker.5e10e14c95a8")}</p>
        </div>
        <Button size="sm" variant="ghost" icon={<FolderPlus className="h-3.5 w-3.5" />} disabled={!selected || busy || status !== 'idle'} onClick={() => setMaking(true)}>{t("ui.components.overlays.WorkspacePicker.84244abc71de")}</Button>
        {mode !== 'select' && <Button
          size="sm"
          variant="secondary"
          disabled={busy || !selected || status !== 'idle'}
          onClick={() => void commit(selected, false)}
          icon={<Check className="h-3.5 w-3.5" />}
        >{t("ui.components.overlays.WorkspacePicker.6f201b36f533")}</Button>}
        <Button
          size="sm"
          variant="primary"
          disabled={busy || !selected || status !== 'idle' || checkoutStatus !== 'ready' || checkout?.cwd !== selected}
          onClick={() => void commit(selected, true)}
        >{t(mode === 'select' ? 'ui.newConversation.useProject' : 'ui.components.overlays.WorkspacePicker.9246c4832cf4')}</Button>
      </footer>
    </Overlay>
  )
}
