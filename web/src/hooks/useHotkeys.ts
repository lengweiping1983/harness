import { useEffect } from 'react'
import { hasModifier, isEditableTarget } from '@/lib/utils'
import { HOTKEYS, matchesHotkey, matchesNewSessionHotkey } from '@/lib/hotkeyCatalog'
import { requestScreenshot } from '@/lib/screenshotCommand'
import { useAppStore } from '@/store/app'
import type { InspectorTab } from '@/types'

/**
 * 全局快捷键。
 *
 * 只在一个地方注册，避免各组件各挂一个 document 监听互相打架。约定：
 *   - 在输入框里不打字（`isEditableTarget`）时，无修饰键快捷键才生效；
 *   - 浮层（命令面板 / 设置 / 快捷键说明 / 灯箱）打开时，Esc 交给最上层自己处理，
 *     这里不重复关闭，避免"一次按键关两层"；
 *   - 所有带 ⌘/Ctrl 的组合在任何输入状态下都可用 —— 输入到一半按 Ctrl+B
 *     应该切换侧栏，而不是把侧栏里的文字删掉。
 *
 * 数字键 1 打开设置中的内置工具，2/3 分别打开检查器的计划和上下文。
 * 检查器页签映射集中维护，避免入口迁移后继续打开已删除的页签。
 */

const TAB_KEYS: Array<[string, InspectorTab]> = [
  ['2', 'todos'],
  ['3', 'context'],
]

export interface HotkeyHandlers {
  onNewSession?: () => void
  onExport?: () => void
}

export function useHotkeys(handlers: HotkeyHandlers = {}) {
  const setCommandOpen = useAppStore((s) => s.setCommandOpen)
  const setShortcutsOpen = useAppStore((s) => s.setShortcutsOpen)
  const setSettingsOpen = useAppStore((s) => s.setSettingsOpen)
  const closeLightbox = useAppStore((s) => s.closeLightbox)
  const toggleSidebar = useAppStore((s) => s.toggleSidebar)
  const toggleInspector = useAppStore((s) => s.toggleInspector)
  const toggleTheme = useAppStore((s) => s.toggleTheme)
  const commandOpen = useAppStore((s) => s.commandOpen)
  const shortcutsOpen = useAppStore((s) => s.shortcutsOpen)
  const settingsOpen = useAppStore((s) => s.settingsOpen)
  const conversationSearchOpen = useAppStore((s) => s.conversationSearchOpen)
  const quickOpen = useAppStore((s) => s.quickOpen)
  const lightboxSrc = useAppStore((s) => s.lightboxSrc)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return
      // AltGr 在部分 Windows 键盘上同时带 Ctrl 和 Alt，字符输入应由编辑器处理。
      if (event.altKey || event.getModifierState('AltGraph')) return
      const mod = hasModifier(event)
      const editable = isEditableTarget(event.target)
      const overlayOpen = Boolean(lightboxSrc || commandOpen || shortcutsOpen || settingsOpen || conversationSearchOpen || quickOpen)
      if (matchesHotkey(event, HOTKEYS.screenshot) || matchesHotkey(event, HOTKEYS.screenshotHidden)) {
        if (!event.repeat && !overlayOpen && requestScreenshot(matchesHotkey(event, HOTKEYS.screenshotHidden))) event.preventDefault()
        return
      }
      if (mod && event.shiftKey && !event.altKey && event.key.toLowerCase() === 'g' && !overlayOpen && useAppStore.getState().activeSessionId) {
        event.preventDefault()
        useAppStore.getState().setInspectorTab('git')
        return
      }
      if (event.code === 'Backquote' && event.ctrlKey && !event.altKey && !event.metaKey && !overlayOpen) {
        event.preventDefault()
        useAppStore.getState().setInspectorTab(event.shiftKey ? 'browser' : 'terminal')
        return
      }
      // Shell editing shortcuts (Ctrl+B/F/R/L, Esc, etc.) belong to xterm.
      // Cmd shortcuts and Ctrl+Shift combinations remain available to the app.
      if ((event.target as HTMLElement)?.closest('.xterm') && !event.metaKey && !event.shiftKey) return

      // Esc 由最上层的浮层自己消费；这里只在"什么都没有"时放行，
      // 好让 Composer 的停止生成逻辑拿到这个键。
      if (event.key === 'Escape' && !overlayOpen) return

      if (matchesNewSessionHotkey(event) && !overlayOpen) {
        event.preventDefault()
        handlers.onNewSession?.()
        return
      }

      if (mod) {
        switch (event.key.toLowerCase()) {
          case 'p': {
            const state = useAppStore.getState()
            if (event.shiftKey || event.altKey || !state.activeSessionId) return
            if (state.quickOpen) {
              event.preventDefault()
              document.querySelector<HTMLInputElement>('[data-quick-open]')?.focus()
            } else if (!overlayOpen) {
              event.preventDefault()
              state.setQuickOpen(true)
            }
            return
          }
          case 'f': {
            const state = useAppStore.getState()
            if (state.conversationSearchOpen && !event.shiftKey && !event.altKey) {
              event.preventDefault()
              document.querySelector<HTMLInputElement>('[data-conversation-search]')?.focus()
              return
            }
            if (event.shiftKey || event.altKey || overlayOpen || !state.activeSessionId) return
            event.preventDefault()
            state.setConversationSearchOpen(true)
            document.querySelector<HTMLInputElement>('[data-conversation-search]')?.focus()
            return
          }
          case 'k':
            event.preventDefault()
            useAppStore.getState().setQuickOpen(false)
            setCommandOpen(!useAppStore.getState().commandOpen)
            return
          case 'b':
            event.preventDefault()
            toggleSidebar()
            return
          case 'j':
            event.preventDefault()
            toggleInspector()
            return
          case ',':
            event.preventDefault()
            useAppStore.getState().setQuickOpen(false)
            setSettingsOpen(!useAppStore.getState().settingsOpen)
            return
          case '\\':
            event.preventDefault()
            useAppStore.getState().setInspectorTab('files')
            return
          default:
            break
        }

        if (event.key === '1') {
          event.preventDefault()
          setSettingsOpen(true, 'tools')
          return
        }
        const tab = TAB_KEYS.find(([key]) => key === event.key)
        if (tab) {
          event.preventDefault()
          useAppStore.getState().setInspectorTab(tab[1])
          return
        }

        if (event.key === '?') {
          event.preventDefault()
          setShortcutsOpen(!useAppStore.getState().shortcutsOpen)
          return
        }
        if (event.shiftKey && event.key.toLowerCase() === 'e') {
          event.preventDefault()
          handlers.onExport?.()
          return
        }
        if (event.key.toLowerCase() === 'd' && event.shiftKey) {
          event.preventDefault()
          toggleTheme()
          return
        }
        return
      }

      if (editable) return

      // 无修饰键
      if (event.key === '/' && !overlayOpen) {
        event.preventDefault()
        setCommandOpen(true)
        return
      }
      if (event.key === '?') {
        event.preventDefault()
        setShortcutsOpen(true)
        return
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [
    closeLightbox,
    commandOpen,
    conversationSearchOpen,
    quickOpen,
    handlers,
    lightboxSrc,
    setCommandOpen,
    setSettingsOpen,
    setShortcutsOpen,
    settingsOpen,
    shortcutsOpen,
    toggleInspector,
    toggleSidebar,
    toggleTheme,
  ])
}
