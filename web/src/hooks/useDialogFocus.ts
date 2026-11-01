import {useLayoutEffect, useRef, type RefObject} from 'react'

let overlayScrollLocks = 0
let overlayPreviousOverflow = ''
const topDialog = () => Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]')).at(-1)

/** 普通浮层与原位展开图表共用顶层键盘、焦点及页面滚动归属。 */
export function useDialogFocus(dialogRef: RefObject<HTMLElement | null>, active: boolean,
  onClose: () => void, onEscape?: (event: KeyboardEvent) => boolean) {
  const originRef = useRef(document.activeElement as HTMLElement | null)
  const wasActive = useRef(false)
  if (active && !wasActive.current) originRef.current = document.activeElement as HTMLElement | null
  wasActive.current = active
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  const escapeRef = useRef(onEscape)
  escapeRef.current = onEscape
  useLayoutEffect(() => {
    if (!active) return
    const dialog = dialogRef.current
    const previousFocus = originRef.current
    const focusable = () => Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])') ?? []).filter((node) => node.tabIndex >= 0 && node.getClientRects().length > 0)
    const frame = requestAnimationFrame(() => {
      if (topDialog() === dialogRef.current && !dialogRef.current?.contains(document.activeElement)) (focusable()[0] ?? dialogRef.current)?.focus()
    })
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      const top = topDialog()
      if (top !== dialogRef.current) return
      if (event.isComposing || event.keyCode === 229) return
      if (event.key === 'Escape') {
        if (escapeRef.current?.(event)) { event.preventDefault(); event.stopPropagation(); return }
        event.preventDefault()
        event.stopPropagation()
        closeRef.current()
      } else if (event.key === 'Tab') {
        const nodes = focusable()
        const first = nodes[0]
        const last = nodes.at(-1)
        if (!first) { event.preventDefault(); dialogRef.current?.focus(); return }
        if (!dialogRef.current?.contains(document.activeElement) || event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) {
          event.preventDefault()
          ;(event.shiftKey ? last : first)?.focus()
        }
      }
    }
    document.addEventListener('keydown', onKey, true)
    if (overlayScrollLocks === 0) overlayPreviousOverflow = document.body.style.overflow
    overlayScrollLocks += 1
    document.body.style.overflow = 'hidden'
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener('keydown', onKey, true)
      overlayScrollLocks -= 1
      if (overlayScrollLocks === 0) document.body.style.overflow = overlayPreviousOverflow
      if ((dialog?.contains(document.activeElement) || document.activeElement === document.body) && previousFocus?.isConnected) previousFocus.focus()
    }
  }, [active, dialogRef])

}
