import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { t } from '@/i18n'
import { cn } from '@/lib/utils'
import { Tooltip } from '@/components/ui/Tooltip'

import type { MessageNavigationEntry as Entry } from '@/lib/messageNavigation'

export function MessageNavigator({ entries, visibleIndex, onJump }: {
  entries: Entry[]; visibleIndex: number
  onJump: (entry: Entry) => void
}) {
  const [hovered, setHovered] = useState<number | null>(null)
  const [availableHeight, setAvailableHeight] = useState(0)
  const container = useRef<HTMLElement>(null)
  const [host, setHost] = useState<HTMLElement | null>(null)
  useLayoutEffect(() => {
    setHost(container.current?.closest<HTMLElement>('[data-message-navigation-host]') ?? null)
  }, [entries.length > 0])
  const rail = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const node = container.current
    if (!node) return
    const observer = new ResizeObserver(() => setAvailableHeight(node.clientHeight))
    observer.observe(node)
    setAvailableHeight(node.clientHeight)
    return () => observer.disconnect()
  }, [entries.length > 0, host])
  const markerHeight = availableHeight > 0 ? Math.min(12, availableHeight / entries.length) : 12
  const active = Math.max(0, entries.findLastIndex(entry => entry.index >= 0 && entry.index <= visibleIndex))
  const activeId = entries[active]?.id
  useEffect(() => {
    if (hovered !== null) return
    const node = rail.current
    const marker = node?.querySelector<HTMLElement>('[aria-current="location"]')
    if (!node || !marker) return
    const bounds = node.getBoundingClientRect(), target = marker.getBoundingClientRect()
    if (target.top < bounds.top) node.scrollTop += target.top - bounds.top
    else if (target.bottom > bounds.bottom) node.scrollTop += target.bottom - bounds.bottom
  }, [activeId, hovered, entries.length])
  if (!entries.length) return null
  const center = hovered ?? active
  const navigation = <nav ref={container} aria-label={t('ui.messageNavigator.title')}
    className="absolute bottom-16 left-0 top-16 z-20 flex w-7 flex-col justify-center sm:left-1"
    onMouseLeave={() => setHovered(null)} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setHovered(null) }}>
    <div ref={rail} className="min-h-0 overflow-y-auto overscroll-contain [scrollbar-width:none] [&>span]:flex">
      {entries.map((entry, index) => {
        const distance = Math.abs(index - center)
        const width = hovered === null ? (index === active ? 14 : 6) : Math.max(6, 24 - distance * 5)
        const label = t('ui.messageNavigator.message', { number: index + 1 })
        return <Tooltip key={entry.id} side="right" delay={100} content={<>
          <span className="line-clamp-4 whitespace-pre-wrap break-words text-xs">{entry.text || t('ui.messageNavigator.empty')}</span>
        </>}>
          <button type="button" data-message-marker={entry.id} aria-label={label}
            aria-current={index === active ? 'location' : undefined}
            onMouseEnter={() => setHovered(index)} onFocus={() => setHovered(index)}
            onClick={() => onJump(entry)}
            style={{ height: markerHeight }}
            className="flex w-7 shrink-0 items-center rounded pl-1 focus-visible:outline focus-visible:outline-brand">
            <span aria-hidden className={cn('max-h-full h-0.5 rounded-full transition-width duration-150 motion-reduce:transition-none',
              index === center ? 'bg-ink' : 'bg-muted')} style={{ width }} />
          </button>
        </Tooltip>
      })}
    </div>
  </nav>
  return host ? createPortal(navigation, host) : navigation
}
