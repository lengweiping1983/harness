import {SessionListSkeleton} from '@/components/sidebar/SessionListSkeleton'
import {useSessionPanelOpener} from '@/hooks/useSessionPanelOpener'
import {noticeText} from '@/lib/noticeText'
import { createPanelFocusFrames } from '@/lib/panelFocus'
import {CollaborationButton} from '@/components/shared/CollaborationButton'
import {useDesktopMenu} from '@/hooks/useDesktopMenu'
import {useAppTheme} from '@/hooks/useAppTheme'
import {SessionRunningIndicator} from '@/components/chat/SessionRunningIndicator'
import {useSessionActivity} from '@/store/sessionActivity'
import {MessageEditContext} from '@/components/chat/MessageEditContext'
import { useLeadConversation } from '@/hooks/useLeadConversation'
import { mainConversationBlocks } from '@/lib/memberDock'
import type { TeamAddress } from '@/lib/agentMount'
import {DesktopServiceStatus} from '@/components/desktop/DesktopServiceStatus'
import { t, useLocale } from '@/i18n'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { RefObject } from 'react'
import type { AttachmentInfo } from '@/types'
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { useActiveSession, useAppStore } from '@/store/app'
import { api } from '@/lib/api'
import { useChat, displayBlocks } from '@/store/chat'
import { useHotkeys } from '@/hooks/useHotkeys'
import { useSessionSocket } from '@/hooks/useSessionSocket'
import { cn } from '@/lib/utils'
import { solveLayout, resolvePanelDrag } from '@/lib/layout'
import { Button, EmptyHint } from '@/components/ui/primitives'
import { ConversationPane } from '@/components/chat/ConversationPane'
import { ReadinessCheck } from '@/components/chat/ReadinessCheck'
import { Composer } from '@/components/chat/Composer'
import { GoalDock } from '@/components/chat/GoalDock'
import { ProjectSidebar } from '@/components/sidebar/ProjectSidebar'
import { Inspector } from '@/components/inspector/Inspector'
import { WorkspaceAction } from '@/components/ui/WorkspaceAction'
import { sessionTitle } from '@/lib/sessionTitle'
import {Toaster} from '@/components/overlays/Toaster'
import {AppOverlays} from '@/components/overlays/AppOverlays'

/**
 * 应用外壳。
 *
 * 三栏：侧栏 / 对话 / 检查器。用户意图仍然只是两个布尔（sidebarOpen /
 * inspectorOpen），但列宽不是「布尔直接映射成两列」——那会在窗口变窄时把
 * 对话区压成一条缝。真正的几何来自 lib/layout 的纯函数解算：对话是唯一不可
 * 牺牲的内容，空间不足时先压右栏、再撤掉右栏轨道。窄屏下两侧变成互斥抽屉，
 * 一次只显示一个（两个抽屉并排会把对话挤到只剩 300px 出头）。
 */

export default function App() {
  useDesktopMenu()
  useAppTheme()
  const locale = useLocale()

  useEffect(() => {
    const refresh = () => { if (!document.hidden) void useAppStore.getState().refreshLanguage() }
    const changed = (event: StorageEvent) => { if (event.key === 'harness.language.changed.v1') refresh() }
    window.addEventListener('storage', changed)
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => { window.removeEventListener('storage', changed); window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', refresh) }
  }, [])
  const booted = useAppStore((s) => s.booted)
  const booting = useAppStore((s) => s.booting)
  const bootError = useAppStore((s) => s.bootError)
  const boot = useAppStore((s) => s.boot)
  const sidebarOpen = useAppStore((s) => s.sidebarOpen)
  const inspectorOpen = useAppStore((s) => s.inspectorOpen)
  const inspectorExpanded = useAppStore((s) => s.inspectorExpanded)
  const toggleInspectorExpanded = useAppStore((s) => s.toggleInspectorExpanded)
  const title = sessionTitle(useActiveSession())
  const activeSessionId = useAppStore((s) => s.activeSessionId)
  const startNewSession = useAppStore((s) => s.startNewSession)
  const drawerIntent = useAppStore((s) => s.drawerIntent)
  const setLayoutMode = useAppStore((s) => s.setLayoutMode)
  const closePanels = useAppStore((s) => s.closeDrawer)

  const channel = useSessionSocket(activeSessionId)
  const chat = useChat(activeSessionId)
  const {request: goalOpenRequest, open: openGoal} = useSessionPanelOpener(activeSessionId)
  const {request: collaborationOpenRequest, open: openCollaboration, close: closeCollaboration} = useSessionPanelOpener(activeSessionId)

  const exportSession = useCallback(() => {
    if (!activeSessionId) return
    window.open(api.exportUrl(activeSessionId), '_blank', 'noopener')
  }, [activeSessionId])

  useHotkeys({ onNewSession: () => void startNewSession(), onExport: exportSession })


  useEffect(() => {
    if (!booted && !booting) void boot()
  }, [boot, booted, booting])

  // 列宽由 lib/layout 解算，视口宽度用 ResizeObserver 读外壳自身：
  // 面板型宿主（IDE 内嵌浏览器）挂载瞬间 window.innerWidth 并不可信，
  // 而 ResizeObserver 报的是布局落定后的真实可用宽度。
  const shellRef = useRef<HTMLDivElement | null>(null)
  const viewportWidth = useElementWidth(shellRef, !(booting && !booted) && !bootError)
  const [rightWidth, setRightWidth] = useState<number | undefined>()
  const panelDrag = useRef<{ start: number; width: number; right: number } | null>(null)
  const layout = useMemo(
    () => solveLayout({ viewportWidth, sidebarOpen, inspectorOpen, inspectorExpanded, rightWidth, drawerIntent: drawerIntent ?? 'none' }),
    [viewportWidth, sidebarOpen, inspectorOpen, inspectorExpanded, rightWidth, drawerIntent, locale],
  )

  const settingsOpen = useAppStore(state => state.settingsOpen)
  const panelsOpen = layout.visibleDrawer !== 'none'
  useSessionActivity(booted && !settingsOpen && !panelsOpen && !inspectorExpanded)
  const sidebarVisible = layout.mode === 'overlay' ? layout.visibleDrawer === 'left' : layout.sidebar > 0
  const inspectorVisible = layout.mode === 'overlay' ? layout.visibleDrawer === 'right' : layout.rightbar > 0
  const mainRef = useRef<HTMLElement | null>(null)
  const drawerFocus = useMemo(createPanelFocusFrames, [])
  useLayoutEffect(() => () => drawerFocus.dispose(), [drawerFocus])
  const drawerOrigin = useRef<{ which: string; element: HTMLElement | null } | null>(null)
  useEffect(() => setLayoutMode(layout.mode), [layout.mode, setLayoutMode])
  useLayoutEffect(() => {
    // Capture the invoking control before inert blurs it or a child moves focus.
    if (panelsOpen && drawerOrigin.current?.which !== layout.visibleDrawer) {
      drawerOrigin.current = { which: layout.visibleDrawer, element: document.activeElement as HTMLElement | null }
    }
    if (!panelsOpen) drawerOrigin.current = null
    if (mainRef.current) mainRef.current.inert = panelsOpen || inspectorExpanded
    const left = shellRef.current?.querySelector<HTMLElement>('#project-sidebar')
    const right = shellRef.current?.querySelector<HTMLElement>('#task-inspector')
    if (left) left.inert = !sidebarVisible
    if (right) right.inert = !inspectorVisible
  }, [booted, panelsOpen, sidebarVisible, inspectorVisible, inspectorExpanded, layout.visibleDrawer])
  useEffect(() => {
    if (!panelsOpen) return
    const which = layout.visibleDrawer
    const panel = shellRef.current?.querySelector<HTMLElement>(`.panel-${which === 'left' ? 'left' : 'right'}`)
    const origin = drawerOrigin.current?.element ?? document.activeElement as HTMLElement | null
    drawerFocus.open(panel ?? null)
    const onKey = (event: KeyboardEvent) => {
      const state = useAppStore.getState()
      if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing || event.keyCode === 229 || state.commandOpen || state.conversationSearchOpen || state.settingsOpen || state.shortcutsOpen || state.lightboxSrc || state.workspacePickerOpen || state.modelMenuOpen) return
      event.preventDefault()
      event.stopPropagation()
      closePanels()
    }
    // Inner controls (for example file search) consume Escape before the drawer.
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      // Restore after visibility changes, when the invoking pane accepts focus.
      drawerFocus.close(() => {
        const otherToggle = origin?.dataset.panelToggle && origin.dataset.panelToggle !== which
        const target = !otherToggle && origin?.isConnected && origin.getClientRects().length ? origin : shellRef.current?.querySelector<HTMLElement>(`[data-panel-toggle="${which}"]`)
        if (target?.isConnected && !target.closest('[inert]')) target.focus()
      })
    }
  }, [closePanels, layout.visibleDrawer, panelsOpen, drawerFocus])

  const onSend = useCallback(
    (text: string, attachments?: AttachmentInfo[], teamAddress?: TeamAddress) => {
      if (!activeSessionId) {
        return useAppStore.getState().materializeSession({ id: crypto.randomUUID(), text, attachments: attachments ?? [], teamAddress, action: 'send', status: 'sending' })
      }
      return channel.send(text, attachments, teamAddress)
    },
    [channel, activeSessionId],
  )

  const leadConversation = useLeadConversation(activeSessionId, chat.blocks)
  const visibleBlocks = useMemo(() => mainConversationBlocks(displayBlocks(chat), leadConversation), [chat.blocks, chat.draft, locale, leadConversation])
  const streaming = chat.running && (Boolean(chat.draft.text || chat.draft.thinking) || visibleBlocks.some(block => block.kind === 'assistant' && block.streaming))

  if (booting && (!booted || bootError)) {
    return (
      <div aria-busy="true" className="flex h-dvh bg-canvas text-muted">
        <aside className="hidden w-64 shrink-0 border-r border-line sm:block"><SessionListSkeleton /></aside>
        <div className="flex min-w-0 flex-1 items-center justify-center gap-2 text-sm">
          <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin text-brand" /><span role="status">{t("ui.App.f76a8d866552")}</span>
        </div>
      </div>
    )
  }

  if (bootError) {
    return (
      <div className="desktop-boot-error flex h-dvh items-center justify-center bg-canvas px-6">
        <EmptyHint
          icon={<AlertTriangle className="h-6 w-6 text-danger" />}
          title={t("ui.App.354dca62058c")}
          description={noticeText(bootError)}
          action={
            <Button
              variant="primary"
              className="mt-2"
              icon={<RefreshCw className="h-3.5 w-3.5" />}
              onClick={() => void boot()}
            >{t("ui.App.b8784c8dd563")}</Button>
          }
        />
      </div>
    )
  }

  return (
    <div
      ref={shellRef}
      className={cn(
        'app-shell relative grid h-dvh overflow-hidden bg-canvas text-ink',
      )}
      style={{
        gridTemplateColumns: `${layout.sidebar}px ${inspectorExpanded ? '0px' : 'minmax(0, 1fr)'} ${layout.rightbar}px`,
      }}
      data-mode={layout.mode}
      data-sidebar={sidebarOpen ? '1' : '0'}
      data-inspector={inspectorOpen ? '1' : '0'}
      data-inspector-expanded={inspectorExpanded ? '1' : '0'}
      data-panels={panelsOpen ? '1' : '0'}
    >
      <aside
        id="project-sidebar"
        aria-label={t("ui.App.9a9b027be74d")}
        aria-hidden={!sidebarVisible}
        {...(!sidebarVisible ? { inert: '' } : {})}
        className="panel panel-left col-start-1 row-start-1 min-w-0 overflow-hidden"
        data-open={layout.visibleDrawer === 'left' ? '1' : '0'}
        data-intent={sidebarOpen ? '1' : '0'}
      >
        <ProjectSidebar />
      </aside>

      {/* data-empty 让 styles.css 把 hero + 输入卡当成**一个整体**垂直居中。
          之前 hero 自己撑满中区、输入框钉在底部，两者之间空出一大片死区，
          空会话看上去像"没加载完"而不是"准备好开始了"。
          保持会话面板的堆叠顺序。 */}
      <MessageEditContext.Provider value={{sessionId: activeSessionId, channel}}><ConversationPane
        ref={mainRef}
        aria-hidden={inspectorExpanded || panelsOpen}
        className="col-start-2 row-start-1"
        empty={chat.blocks.length === 0}
        notice={chat.blocks.length === 0 ? <ReadinessCheck /> : undefined}
        title={title}
        titleIndicator={<SessionRunningIndicator sessionId={activeSessionId} />}
        headerLeading={!sidebarVisible && <WorkspaceAction action="expand-left" label={t('ui.sidebar.expand')} data-panel-toggle="left" aria-controls="project-sidebar" onClick={() => useAppStore.getState().toggleSidebar()} />}
        headerTrailing={<>
          <CollaborationButton sessionId={activeSessionId} open={Boolean(activeSessionId && collaborationOpenRequest?.sessionId === activeSessionId)} onOpen={openCollaboration} onClose={closeCollaboration} />
          {!inspectorVisible && <WorkspaceAction action="new-tab" label={t('ui.trace.hub')} aria-controls="task-inspector" aria-expanded={false} data-panel-toggle="right" onClick={() => useAppStore.getState().setInspectorTab('home')} />}
        </>}
        conversation={{
          turns: chat.turnActivity, history: chat.history, onRefreshHistory: channel.refreshHistory,
          onReconnect: activeSessionId ? channel.reconnect : undefined, blocks: visibleBlocks,
          running: chat.running, streaming, connection: chat.connection,
        }}
      >
            <Composer
              showSuggestions={chat.blocks.length === 0}
              goalPanel={activeSessionId && <GoalDock key={activeSessionId} sessionId={activeSessionId} state={chat.goalState} running={chat.running}
              openRequest={goalOpenRequest?.sessionId === activeSessionId ? goalOpenRequest.serial : 0} />}
              steering={chat.steering}
              generation={chat.generation}
              todos={chat.todos}
              running={chat.running}
              stopping={chat.stopping}
              queued={chat.queued}
              queuePaused={chat.queuePaused}
              planMode={chat.planMode}
              planPending={chat.planModeState.pending}
              connection={activeSessionId ? chat.connection : 'open'}
              onSend={onSend}
              onCancel={channel.cancel}
              onTogglePlan={channel.togglePlan}
              onOpenGoal={openGoal}
            />
      </ConversationPane></MessageEditContext.Provider>

      <aside
        id="task-inspector"
        aria-label={t("ui.App.5c8cdb5d982a")}
        aria-hidden={!inspectorVisible}
        {...(!inspectorVisible ? { inert: '' } : {})}
        className="panel panel-right col-start-3 row-start-1 min-w-0 overflow-hidden"
        data-open={layout.visibleDrawer === 'right' ? '1' : '0'}
        data-intent={inspectorOpen ? '1' : '0'}
        onDoubleClick={(event) => {
          const target = event.target as HTMLElement
          // Only structural whitespace toggles; text selection and controls keep their usual behavior.
          if (target.closest('button, a, input, textarea, select, label, summary, pre, code, svg, img, video, iframe, [role="button"], [role="tab"], [contenteditable]')) return
          if (Array.from(target.childNodes).some((node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim())) return
          toggleInspectorExpanded()
        }}
      >
        <Inspector visible={inspectorVisible} />
      </aside>

      {viewportWidth >= 1024 && <div
        role="separator" tabIndex={0} aria-orientation="vertical"
        aria-label={t("ui.App.5c8cdb5d982a")}
        aria-valuemin={0} aria-valuemax={Math.max(0, viewportWidth - layout.sidebar)} aria-valuenow={layout.rightbar}
        className="workspace-divider"
        style={{ left: Math.max(layout.sidebar, Math.min(viewportWidth - 10, viewportWidth - layout.rightbar - 5)) }}
        onPointerDown={event => {
          if (event.button !== 0) return
          event.preventDefault()
          event.currentTarget.setPointerCapture(event.pointerId)
          panelDrag.current = { start: event.clientX, width: layout.rightbar, right: layout.rightbar }
        }}
        onPointerMove={event => {
          const drag = panelDrag.current
          if (!drag) return
          drag.right = Math.max(0, Math.min(viewportWidth - layout.sidebar, drag.width + drag.start - event.clientX))
          setRightWidth(drag.right)
          useAppStore.setState({ inspectorOpen: true, inspectorExpanded: false })
        }}
        onPointerUp={event => {
          const drag = panelDrag.current
          if (!drag) return
          panelDrag.current = null
          useAppStore.setState(resolvePanelDrag(viewportWidth - layout.sidebar, drag.right))
          event.currentTarget.releasePointerCapture(event.pointerId)
        }}
        onPointerCancel={() => { panelDrag.current = null }}
        onLostPointerCapture={() => { panelDrag.current = null }}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing || event.keyCode === 229) return
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
          event.preventDefault()
          const available = viewportWidth - layout.sidebar
          const right = event.key === 'Home' ? available : event.key === 'End' ? 0 : layout.rightbar + (event.key === 'ArrowLeft' ? 32 : -32)
          setRightWidth(right)
          useAppStore.setState(resolvePanelDrag(available, right))
        }}
      />}

      <DesktopServiceStatus />
      {/* 浮层 */}
      <AppOverlays />
      <Toaster />

      {/* 窄屏遮罩：点击关闭抽屉。显隐完全由 CSS 决定，见 styles.css 的 .panel-scrim。 */}
      <button type="button" className="panel-scrim" onClick={closePanels} aria-label={t('ui.panels.close')} />

    </div>
  )
}

/**
 * 用 ResizeObserver 读元素的实际可用宽度。
 *
 * 不用 window.innerWidth：面板型宿主（IDE 内嵌浏览器、侧边栏窗口）挂载瞬间
 * 读到的窗口宽度可能是 0 或临时值，用它算列宽会让主内容区塌成一条窄缝。
 * ResizeObserver 报的是布局落定后的真实宽度，也天然覆盖容器尺寸变化
 * （不只是窗口 resize）。
 */
function useElementWidth(ref: RefObject<HTMLElement | null>, mounted: boolean): number {
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const node = ref.current
    if (!mounted || !node) return
    let frame = 0
    const read = () => {
      frame = 0
      const next = node.clientWidth
      setWidth((prev) => (prev === next ? prev : next))
    }
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(read)
    }
    const observer = new ResizeObserver(schedule)
    observer.observe(node)
    read()
    schedule()
    window.addEventListener('orientationchange', schedule)
    document.addEventListener('visibilitychange', schedule)
    return () => {
      if (frame) cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('orientationchange', schedule)
      document.removeEventListener('visibilitychange', schedule)
    }
  }, [ref, mounted])
  return width
}
