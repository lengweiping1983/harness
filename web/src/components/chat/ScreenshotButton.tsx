import {newIdentity} from '@/lib/ids'
import type {ResourceCopy} from '@/types'
import {noticeText} from '@/lib/noticeText'
import {ApiError} from '@/lib/http'
import {ResourceError,failureText} from '@/lib/resourceError'
import { t, useLocale } from '@/i18n'
import { useEffect, useLayoutEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, Loader2, Scissors } from 'lucide-react'
import { api } from '@/lib/api'
import { captureDisplay, pngFile } from '@/lib/screenshot'
import { useAppStore } from '@/store/app'
import { Tooltip } from '@/components/ui/primitives'
import { useAnchoredMenu } from '@/hooks/useAnchoredMenu'
import { ScreenshotCrop } from './ScreenshotCrop'
import {formatHotkey, HOTKEYS} from '@/lib/hotkeyCatalog'
import {registerScreenshotCommand} from '@/lib/screenshotCommand'
import type {DesktopScreenshotResult} from '@/lib/systemPermissions'

export function ScreenshotButton({ addFiles, focusEditor }: { addFiles: (files: File[]) => Promise<void>; focusEditor: () => void }) {
  useLocale()

  const [capabilities, setCapabilities] = useState<{ native: boolean; hideWindow: boolean; reason: ResourceCopy | null } | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [cleanupFailed, setCleanupFailed] = useState(false)
  const finishing = useRef<Promise<void> | null>(null)
  const [source, setSource] = useState<Blob | string | null>(null)
  const [initialFullSelection, setInitialFullSelection] = useState(false)
  const pendingAdd = useRef(addFiles)
  const pendingFocus = useRef(focusEditor)
  const active = useRef(false)
  const scope = useRef(0)
  const mounted = useRef(true)
  const anchor = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const menuId = useId()
  const style = useAnchoredMenu(anchor, open, 260)
  const toast = useAppStore((state) => state.toast)
  const browserSupported = Boolean(navigator.mediaDevices?.getDisplayMedia)
  const desktop = (window as unknown as {harnessDesktop?: {screenshot?: (action: 'capture' | 'finish', hideWindow?: boolean) => Promise<DesktopScreenshotResult>}}).harnessDesktop
  const supported = Boolean(desktop?.screenshot || capabilities?.native || browserSupported)
  const canHide = Boolean(desktop?.screenshot || capabilities?.hideWindow)
  useLayoutEffect(() => {
    mounted.current = true
    const unsubscribe = useAppStore.subscribe((state, previous) => {
      if (state.activeSessionId !== previous.activeSessionId || state.newSessionRevision !== previous.newSessionRevision) scope.current++
    })
    void api.screenshotCapabilities().then((result) => { if (mounted.current) setCapabilities(result.ok ? result.data : { native: false, hideWindow: false, reason: {key: "ui.components.chat.ScreenshotButton.a0db5328de97"} }) })
    return () => { unsubscribe(); scope.current++; mounted.current = false; if (active.current) void finish() }
  }, [])
  useEffect(() => {
    if (!open || style.visibility !== 'visible') return
    menu.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    const dismiss = (event: Event) => {
      if (!menu.current?.contains(event.target as Node) && !anchor.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', dismiss); document.addEventListener('focusin', dismiss)
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('focusin', dismiss) }
  }, [open, style.visibility])
  const finish = () => {
    if (finishing.current) return finishing.current
    if (!active.current) return Promise.resolve()
    if (mounted.current) setSource(null)
    const operation = Promise.resolve().then(() => desktop?.screenshot?.('finish')).then(result => {
      if (result && typeof result === 'object' && 'failure' in result) throw new ResourceError(result.failure)
      active.current = false
      if (mounted.current) { setBusy(false); pendingFocus.current() }
    }).catch(() => {
      toast({title: {key: 'ui.screenshot.cleanupFailed'}, description: {key: 'ui.screenshot.cleanupUnknown'}, tone: 'warn'})
      if (mounted.current) { setBusy(false); setCleanupFailed(true) }
    })
    finishing.current = operation
    void operation.then(() => { if (!active.current) finishing.current = null })
    return operation
  }
  const start = async (hideWindow: boolean) => {
    if (active.current || cleanupFailed || !mounted.current) return
    active.current = true; setBusy(true); setOpen(false)
    setInitialFullSelection(false)
    // Preserve the destination even if the user changes chats during capture.
    const generation = scope.current
    const ownsScope = () => mounted.current && scope.current === generation
    pendingAdd.current = addFiles
    pendingFocus.current = () => { if (ownsScope()) focusEditor() }
    const oldTitle = document.title
    let token = ''
    let cropping = false
    try {
      if (desktop?.screenshot) {
        const data = await desktop.screenshot('capture', hideWindow)
        if (data && typeof data === 'object' && 'failure' in data) throw new ResourceError(data.failure)
        if (data && typeof data === 'object' && 'permission' in data) {
          if (ownsScope()) toast({title:{key:'ui.systemPermission.required'}, description:data.permission, tone:'warn'})
        } else if (data && ownsScope()) { cropping = true; setSource(typeof data === 'string' ? pngFile(data) : 'url' in data ? data.url : new Blob([data.data], {type:data.type})) }
      } else if (capabilities?.native) {
        if (hideWindow) {
          token = 'harness-capture-' + newIdentity()
          document.title = `${oldTitle} [${token}]`
        }
        const result = await api.screenshot(hideWindow, token)
        if (!result.ok) throw new ApiError(result)
        if (!result.data.cancelled && ownsScope()) { cropping = true; setInitialFullSelection(true); setSource(pngFile(result.data.data)) }
      } else {
        if (hideWindow) throw capabilities?.reason ? new ResourceError(capabilities.reason) : new ResourceError({key: "ui.components.chat.ScreenshotButton.35b3caa863b0"})
        // Keep getDisplayMedia in this click handler, before any awaited API call.
        const snapshot = await captureDisplay()
        if (ownsScope()) { cropping = true; setSource(snapshot) }
      }
    } catch (error) {
      if (!ownsScope()) return
      const denied = error instanceof DOMException && error.name === 'NotAllowedError'
      const permissionRequired = error instanceof ApiError && ['screenshot/permission-required','screenshot/accessibility-required'].includes(error.code)
      toast({ title: {key: denied || permissionRequired ? 'ui.systemPermission.required' : "ui.components.chat.ScreenshotButton.aa6477207270"},
        description: denied ? {key:'ui.systemPermission.screen.browser'} : error instanceof Error ? failureText(error) : {key: "ui.components.chat.ScreenshotButton.54a4e4ed731c"}, tone: 'warn' })
    } finally {
      if (token && document.title.includes(token)) document.title = oldTitle
      // Browser crop owns busy state until confirm/cancel.
      if (!cropping) finish()
    }
  }
  useLayoutEffect(() => registerScreenshotCommand(hideWindow => {
    const state = useAppStore.getState()
    if (document.hidden || document.querySelector('[role="dialog"][aria-modal="true"]')
        || state.settingsOpen || state.shortcutsOpen || state.commandOpen || state.conversationSearchOpen || state.quickOpen || state.lightboxSrc || state.workspacePickerOpen
        || active.current || cleanupFailed || !supported || hideWindow && !canHide) return false
    void start(hideWindow)
    return true
  }))
  return <>
    <div className="inline-flex h-7 shrink-0 items-center rounded-xl bg-raised text-muted">
      <Tooltip content={supported ? t('ui.screenshot.shortcutHint', {shortcut: formatHotkey(HOTKEYS.screenshot)}) : t("ui.components.chat.ScreenshotButton.81cbec2af613")}>
        <button type="button" aria-label={busy ? t("ui.components.chat.ScreenshotButton.aca98bb4fefc") : t("ui.components.chat.ScreenshotButton.c95dc99afe57")} disabled={busy || cleanupFailed || !supported} onClick={() => void start(false)}
          className="inline-flex h-7 w-8 items-center justify-center rounded-l-xl hover:bg-line hover:text-ink disabled:opacity-50">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Scissors className="h-4 w-4" />}
        </button>
      </Tooltip>
      <button ref={anchor} type="button" aria-label={t("ui.components.chat.ScreenshotButton.baff2e55a8d0")} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined}
        disabled={busy || cleanupFailed} onClick={() => setOpen(!open)} onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
          if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); setOpen(true) }
        }} className="inline-flex h-7 w-5 items-center justify-center rounded-r-xl border-l border-line hover:bg-line hover:text-ink disabled:opacity-50">
        <ChevronDown className="h-3 w-3" />
      </button>
    </div>
    {open ? createPortal(<div ref={menu} style={style} className="z-popover rounded-2xl border border-line bg-surface p-2 shadow-xl"
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setOpen(false); anchor.current?.focus() }
        if (event.key === 'Tab') { event.preventDefault(); setOpen(false); anchor.current?.focus() }
      }}>
      <div id={menuId} role="menu" aria-label={t("ui.components.chat.ScreenshotButton.baff2e55a8d0")}>
        <button type="button" role="menuitem" aria-disabled={!canHide} onClick={() => {
          if (canHide) void start(true)
          else toast({ title: {key: "ui.components.chat.ScreenshotButton.e3dd73a43278"}, description: capabilities?.reason || {key: "ui.components.chat.ScreenshotButton.ed68c8f2a5d8"}, tone: 'warn' })
        }} className="flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left text-sm hover:bg-raised focus-visible:bg-raised focus-visible:outline-none aria-disabled:opacity-50">
          <Scissors className="h-4 w-4" /><span className="flex-1">{t("ui.components.chat.ScreenshotButton.092645d6b89a")}</span><kbd className="text-xs text-faint">{formatHotkey(HOTKEYS.screenshotHidden)}</kbd></button>
      </div>
      <p className="px-3 pb-1 text-xs leading-relaxed text-faint">{canHide ? t("ui.components.chat.ScreenshotButton.d4c0a1179986") : capabilities?.reason ? noticeText(capabilities.reason) : t("ui.components.chat.ScreenshotButton.ed68c8f2a5d8")}</p>
    </div>, document.body) : null}
    {source ? <ScreenshotCrop source={source} initialFullSelection={initialFullSelection} onCancel={finish} onConfirm={async (file) => { await pendingAdd.current([file]); finish() }} /> : null}
  </>
}
