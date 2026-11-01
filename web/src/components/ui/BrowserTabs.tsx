import { t, useLocale } from '@/i18n'
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface BrowserTab<T extends string> {
  value: T
  label: ReactNode
  icon?: ReactNode
  title?: string
  closeLabel?: string
  closable?: boolean
  panelId?: string
}

/** A single scrolling strip; closing controls are siblings of the tab buttons. */
export function BrowserTabs<T extends string>({ options, value, onChange, onClose, label, panelId, className, trailing }: {
  options: BrowserTab<T>[]
  value: T | null
  onChange: (value: T) => void
  onClose?: (value: T) => void
  label: string
  panelId: string
  className?: string
  trailing?: ReactNode
}) {
  useLocale()

  const id = useId()
  const track = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ left: false, right: false })
  const selected = options.findIndex((option) => option.value === value)
  const focusIndex = selected < 0 ? 0 : selected

  function measure() {
    const node = track.current
    if (node) setEdges({ left: node.scrollLeft > 1, right: node.scrollLeft + node.clientWidth < node.scrollWidth - 1 })
  }
  function revealSelected() {
    const node = track.current
    const tab = node?.querySelector<HTMLElement>('[aria-selected="true"]')?.parentElement
    if (!node || !tab) return
    const strip = node.getBoundingClientRect(), item = tab.getBoundingClientRect()
    if (item.left < strip.left + 28) node.scrollLeft -= strip.left + 28 - item.left
    else if (item.right > strip.right - 28) node.scrollLeft += item.right - strip.right + 28
    measure()
  }
  useEffect(() => {
    const node = track.current
    if (!node) return
    const observer = new ResizeObserver(() => { revealSelected(); measure() })
    observer.observe(node)
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.shiftKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY) || node.scrollWidth <= node.clientWidth) return
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? node.clientWidth : 1)
      const next = Math.max(0, Math.min(node.scrollWidth - node.clientWidth, node.scrollLeft + delta))
      if (next === node.scrollLeft) return
      event.preventDefault()
      node.scrollLeft = next
    }
    node.addEventListener('wheel', wheel, { passive: false })
    return () => { observer.disconnect(); node.removeEventListener('wheel', wheel) }
  }, [])
  useEffect(() => { revealSelected(); measure() }, [value, options.length])

  function select(index: number) {
    const option = options[index]
    if (!option) return
    onChange(option.value)
    track.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[index]?.focus({ preventScroll: true })
  }
  function close(index: number, restoreFocus: boolean) {
    if (!onClose || options[index]?.closable === false) return
    const node = track.current
    const buttons = node?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
    // Follow the right-hand neighbour, falling back to the previous one.
    const next = buttons?.[index + 1] ?? buttons?.[index - 1]
    const active = node?.querySelector<HTMLButtonElement>('[aria-selected="true"]')
    if (restoreFocus) {
      const destination = (selected === index ? next : active ?? next) ?? node?.closest<HTMLElement>('[role="tabpanel"]')
      destination?.focus({ preventScroll: true })
    }
    onClose(options[index]!.value)
  }

  return <div className={cn('browser-tabs', className)}>
    {edges.left ? <button type="button" className="browser-tabs-scroll browser-tabs-scroll-left" aria-label={t("ui.components.ui.BrowserTabs.02dd321b9650", {p0: (label)})} onClick={() => track.current?.scrollBy({ left: -Math.max(160, track.current.clientWidth * .7), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })}><ChevronLeft aria-hidden className="h-3.5 w-3.5" /></button> : null}
    <div ref={track} role="tablist" aria-label={label} className="browser-tabs-track" onScroll={measure}>
      {options.map((option, index) => <div key={option.value} role="presentation" className="browser-tab" data-active={option.value === value ? 'true' : undefined}>
        <button type="button" role="tab" data-tab-value={String(option.value)} id={`${id}-${index}`} aria-controls={option.panelId ?? panelId} aria-selected={option.value === value} tabIndex={index === focusIndex ? 0 : -1} title={option.title} className="browser-tab-select" onClick={() => onChange(option.value)} onKeyDown={(event) => {
          if (event.altKey || event.ctrlKey || event.metaKey || event.nativeEvent.isComposing || event.keyCode === 229) return
          const next = event.key === 'ArrowRight' ? (index + 1) % options.length : event.key === 'ArrowLeft' ? (index + options.length - 1) % options.length : event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : -1
          if (next >= 0) { event.preventDefault(); select(next) }
          else if (event.key === 'Delete' && onClose && option.closable !== false) { event.preventDefault(); close(index, true) }
        }}>{option.icon}<span className="browser-tab-label">{option.label}</span></button>
        {onClose && option.closable !== false ? <button type="button" aria-label={option.closeLabel} title={option.closeLabel} tabIndex={index === focusIndex ? 0 : -1} className="browser-tab-close" onClick={(event) => close(index, event.detail === 0 || event.currentTarget === document.activeElement)}><X aria-hidden className="h-3 w-3" /></button> : null}
      </div>)}
      {trailing ? <div role="presentation" className="browser-tabs-trailing">{trailing}</div> : null}
    </div>
    {edges.right ? <button type="button" className="browser-tabs-scroll browser-tabs-scroll-right" aria-label={t("ui.components.ui.BrowserTabs.ab0364f782f0", {p0: (label)})} onClick={() => track.current?.scrollBy({ left: Math.max(160, track.current.clientWidth * .7), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })}><ChevronRight aria-hidden className="h-3.5 w-3.5" /></button> : null}
  </div>
}
