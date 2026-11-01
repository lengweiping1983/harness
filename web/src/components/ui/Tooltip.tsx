import { useLocale } from '@/i18n'
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/lib/utils'

const useBrowserLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect

/**
 * 定位在触发元素上的浮层提示。
 *
 * 用 fixed + 实测 bounding rect，而不是 CSS 定位：触发器可能在任意
 * overflow 容器里，父级 transform / overflow 都会让 absolute 定位跑偏。
 * 锚点包一层 `inline-flex` 的 span，既不改变布局，也不吞事件。
 */
export function Tooltip({
  content,
  children,
  side = 'top',
  delay = 320,
  className,
  anchorClassName,
  anchorTabIndex,
  anchorSelector,
  onTrigger,
}: {
  content: ReactNode
  children: ReactNode
  side?: 'top' | 'bottom' | 'left' | 'right'
  delay?: number
  className?: string
  anchorClassName?: string
  anchorTabIndex?: number
  anchorSelector?: string
  onTrigger?: () => void
}) {
  useLocale()

  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState({ x: 0, y: 0 })
  const anchor = useRef<HTMLSpanElement | null>(null)
  const tip = useRef<HTMLSpanElement | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const engaged = useRef(false)
  const id = useId()

  const show = useCallback(() => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setOpen(true), delay)
  }, [delay])

  const hide = useCallback(() => {
    if (timer.current) clearTimeout(timer.current)
    setOpen(false)
  }, [])

  useEffect(() => () => timer.current && clearTimeout(timer.current), [])

  useBrowserLayoutEffect(() => {
    if (!open) return
    const anchorBox = (anchorSelector ? anchor.current?.querySelector(anchorSelector) : anchor.current)?.getBoundingClientRect()
    if (!anchorBox) return
    const tipBox = tip.current?.getBoundingClientRect()
    const w = tipBox?.width ?? 0
    const h = tipBox?.height ?? 0
    const gap = 6
    const vw = window.innerWidth
    const vh = window.innerHeight

    let x: number
    let y: number
    if (side === 'top') {
      x = anchorBox.left + anchorBox.width / 2 - w / 2
      y = anchorBox.top - h - gap
      if (y < 4) y = anchorBox.bottom + gap
    } else if (side === 'bottom') {
      x = anchorBox.left + anchorBox.width / 2 - w / 2
      y = anchorBox.bottom + gap
      if (y + h > vh - 4) y = anchorBox.top - h - gap
    } else if (side === 'left') {
      x = anchorBox.left - w - gap
      y = anchorBox.top + anchorBox.height / 2 - h / 2
      if (x < 4) x = anchorBox.right + gap
    } else {
      x = anchorBox.right + gap
      y = anchorBox.top + anchorBox.height / 2 - h / 2
      if (x + w > vw - 4) x = anchorBox.left - w - gap
    }

    // 贴边时夹回视口内，避免被裁掉。
    setPos({ x: Math.min(Math.max(4, x), vw - w - 4), y: Math.min(Math.max(4, y), vh - h - 4) })
  }, [open, side, anchorSelector])

  useEffect(() => {
    if (!open) return
    const onScrollOrResize = () => hide()
    window.addEventListener('scroll', onScrollOrResize, true)
    window.addEventListener('resize', onScrollOrResize)
    return () => {
      window.removeEventListener('scroll', onScrollOrResize, true)
      window.removeEventListener('resize', onScrollOrResize)
    }
  }, [open, hide])

  const hasContent = content !== null && content !== undefined && content !== false && content !== ''
  useEffect(() => { if (hasContent && engaged.current) show() }, [hasContent, show])
  const trigger = () => { engaged.current = true; onTrigger?.(); if (hasContent) show() }
  const leave = () => { engaged.current = false; hide() }

  return (
    <>
      <span
        ref={anchor}
        tabIndex={anchorTabIndex}
        onMouseEnter={hasContent || onTrigger ? trigger : undefined}
        onMouseLeave={hasContent || onTrigger ? leave : undefined}
        onMouseDown={hide}
        onFocus={hasContent || onTrigger ? trigger : undefined}
        onBlur={hasContent || onTrigger ? leave : undefined}
        aria-describedby={open && hasContent ? id : undefined}
        className={cn('inline-flex', anchorClassName)}
      >
        {children}
      </span>
      {open &&
        hasContent &&
        createPortal(
          <span
            ref={tip}
            id={id}
            role="tooltip"
            style={{ left: pos.x, top: pos.y }}
            className={cn(
              'pointer-events-none fixed z-tooltip max-w-xs animate-fade-in rounded-lg',
              'border border-line bg-overlay px-2 py-1 text-2xs leading-relaxed text-ink shadow-pop',
              className,
            )}
          >
            {content}
          </span>,
          document.body,
        )}
    </>
  )
}
