import { useLocale } from '@/i18n'
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal, flushSync } from 'react-dom'
import { cn } from '@/lib/utils'
import { contextMenuBounds } from '@/lib/contextMenu'

export interface ContextMenuAnchor {
  id: string
  x: number
  y: number
  origin: HTMLElement
  initial?: 'first' | 'last'
}
export interface ContextAction { id: string; label: string; icon: ReactNode; run: () => void; danger?: boolean; separatorBefore?: boolean }
const MENU_WIDTH = 208

/** A single keyboard surface, placed using its measured size, independent of rail clipping. */
export function ContextActionMenu({ anchor, label, items, onClose }: {
  anchor: ContextMenuAnchor; label: string; items: ContextAction[]; onClose: () => void
}) {
  useLocale()

  const menu = useRef<HTMLDivElement>(null)
  const [style, setStyle] = useState<React.CSSProperties>({ visibility: 'hidden', width: MENU_WIDTH })
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  const exit = () => {
    flushSync(() => closeRef.current())
    if (anchor.origin.isConnected) anchor.origin.focus({ preventScroll: true })
  }
  useLayoutEffect(() => {
    const node = menu.current
    if (!node) return
    const place = () => {
      const viewport = window.visualViewport
      setStyle(contextMenuBounds(anchor, { width: MENU_WIDTH, height: node.scrollHeight + 2 }, {
        left: viewport?.offsetLeft ?? 0, top: viewport?.offsetTop ?? 0,
        width: viewport?.width ?? innerWidth, height: viewport?.height ?? innerHeight,
      }))
    }
    place()
    const observer = new ResizeObserver(place)
    observer.observe(node)
    const scroll = (event: Event) => {
      if (node.contains(event.target as Node)) return
      closeRef.current()
      if (node.contains(document.activeElement) && anchor.origin.isConnected) anchor.origin.focus({ preventScroll: true })
    }
    window.addEventListener('resize', place)
    window.addEventListener('scroll', scroll, true)
    window.visualViewport?.addEventListener('resize', place)
    window.visualViewport?.addEventListener('scroll', place)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', scroll, true)
      window.visualViewport?.removeEventListener('resize', place)
      window.visualViewport?.removeEventListener('scroll', place)
    }
  }, [anchor])
  useLayoutEffect(() => {
    if (style.visibility === 'hidden') return
    const buttons = menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')
    buttons?.[anchor.initial === 'last' ? buttons.length - 1 : 0]?.focus({ preventScroll: true })
  }, [anchor, style.visibility])
  return createPortal(<>
    <div className="fixed inset-0 z-context-scrim" onClick={exit} onContextMenu={(event) => { event.preventDefault(); exit() }} />
    <div ref={menu} role="menu" aria-label={label} style={style}
      className="fixed z-context-menu overflow-y-auto rounded-xl border border-line bg-surface py-1 shadow-pop"
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing || event.keyCode === 229) {
          if (event.key === 'Enter' || event.key === ' ') event.preventDefault()
          return
        }
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); exit(); return }
        if (event.key === 'Tab') { event.stopPropagation(); exit(); return }
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
          event.preventDefault(); event.stopPropagation()
          const buttons = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [])
          const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
            : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
          buttons[next]?.focus()
        }
      }}>
      {items.map((item) => <div key={item.id} role="none">
        {item.separatorBefore ? <div role="separator" className="mx-3 my-1 border-t border-line" /> : null}
        <button type="button" role="menuitem" tabIndex={-1}
        onClick={() => { exit(); item.run() }}
        className={cn('flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors focus-visible:bg-raised focus-visible:outline-none',
          item.danger ? 'text-danger hover:bg-danger/10' : 'text-muted hover:bg-raised hover:text-ink')}>
        {item.icon}{item.label}
      </button></div>)}
    </div>
  </>, document.body)
}
