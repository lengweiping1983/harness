import { useCallback, type KeyboardEvent } from 'react'

type KeyboardOptions = {
  running: boolean; shortcutsOpen: boolean; setShortcutsOpen: (open: boolean) => void
  onCancel: () => void; submit: () => Promise<void>
  handleCompletionKey: (event: KeyboardEvent<HTMLTextAreaElement>) => boolean
}

/** 输入法、候选菜单、快捷键浮层和发送按固定优先级处理。 */
export function useComposerKeyboard({running, shortcutsOpen, setShortcutsOpen,
  onCancel, submit, handleCompletionKey}: KeyboardOptions) {
  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      // 组词期间回车、方向键和 Escape 属于输入法，不属于 Agent 操作。
      if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
      // 候选菜单打开时，方向键/回车/Tab/Esc 全部归它。
      // 必须在下面所有分支之前拦：否则 Enter 会同时「移动高亮」和「发送消息」，
      // ↑↓ 会同时「换候选」和「移动光标」——textarea 的默认行为抢不掉。
      if (handleCompletionKey(event)) return

      // Esc 优先关浮层，其次取消生成。
      if (event.key === 'Escape') {
        if (shortcutsOpen) {
          setShortcutsOpen(false)
          return
        }
        if (running) {
          event.preventDefault()
          onCancel()
        }
        return
      }
      if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
        event.preventDefault()
        submit()
      }
    },
    [onCancel, running, shortcutsOpen, setShortcutsOpen, submit, handleCompletionKey],
  )

  return onKeyDown
}
