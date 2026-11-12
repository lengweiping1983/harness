import { t, useLocale } from '@/i18n'
import { memo, useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import type { RefObject } from 'react'
import { createPortal } from 'react-dom'
import {
  Command,
  FileText,
  Folder,
  History,
  Sparkles,
  TriangleAlert,
  Loader2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { CandidateKind, ScoredCandidate } from '@/lib/trigger'
import { Kbd, RefreshButton, ShowMoreControl } from '@/components/ui/primitives'
import { useAnchoredMenu } from '@/hooks/useAnchoredMenu'

/**
 * 输入框上方的候选浮层。
 *
 * 刻意保持「哑组件」：候选键盘逻辑由 Composer 委托 useComposerCompletion 处理。
 * 原因是 textarea 的 ↑↓ 默认是移动光标，菜单打开时必须由同一个按键处理器
 * 截走 —— 若菜单自己挂 document 监听，会和 textarea 的默认行为抢同一个按键，
 * 出现「光标跳到上一行、菜单也换了一项」的双重响应。
 */

export interface TriggerMenuProps {
  id: string
  anchor: RefObject<HTMLElement | null>
  items: ScoredCandidate[]
  allItems?: ScoredCandidate[]
  onShowMore?: (kind: CandidateKind) => void
  activeIndex: number
  /** 顶部标题，例如「引用工作区」/「命令」。 */
  title: string
  emptyText?: string
  statusText?: string
  loading?: boolean
  sourceRoot?: string | null
  errorText?: string
  onRetry?: () => void
  onActive?: (item: ScoredCandidate) => void
  onPick: (item: ScoredCandidate) => void
  onClose: () => void
}

const KIND_ICONS: Record<CandidateKind, typeof FileText> = {
  directory: Folder,
  file: FileText,
  skill: Sparkles,
  session: History,
  command: Command,
}

const GROUP_TITLE_KEYS: Record<CandidateKind, string> = {
  directory: 'ui.components.overlays.CommandPalette.52daa71ebc31',
  file: 'ui.components.inspector.Inspector.39932f24fe11',
  skill: 'ui.components.chat.TriggerMenu.99aea2f9131a',
  session: 'ui.components.overlays.CommandPalette.a63280253f17',
  command: 'ui.components.chat.TriggerMenu.928f87d4507b',
}

/** 把命中下标切成高亮片段。连续命中合并成一段，避免满屏碎片。 */
function segments(text: string, hits: number[]): Array<{ text: string; hit: boolean }> {
  if (!hits.length) return [{ text, hit: false }]
  const set = new Set(hits)
  const out: Array<{ text: string; hit: boolean }> = []
  let buffer = ''
  let mode = set.has(0)
  for (let i = 0; i < text.length; i += 1) {
    const on = set.has(i)
    if (on !== mode) {
      if (buffer) out.push({ text: buffer, hit: mode })
      buffer = ''
      mode = on
    }
    buffer += text[i]
  }
  if (buffer) out.push({ text: buffer, hit: mode })
  return out
}

export const TriggerMenu = memo(function TriggerMenu({
  id,
  anchor,
  items,
  activeIndex,
  allItems,
  onShowMore,
  title,
  emptyText = t("ui.components.chat.TriggerMenu.8151e8704a7c"),
  statusText,
  loading = false,
  sourceRoot,
  errorText,
  onRetry,
  onPick,
  onActive,
  onClose,
}: TriggerMenuProps) {
  const locale = useLocale()

  const menuRef = useRef<HTMLDivElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const activeRef = useRef<HTMLButtonElement | null>(null)
  const style = useAnchoredMenu(anchor, true, 'anchor')

  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !menuRef.current?.contains(event.target) && !anchor.current?.contains(event.target)) onClose()
    }
    document.addEventListener('pointerdown', dismiss, true)
    return () => document.removeEventListener('pointerdown', dismiss, true)
  }, [anchor, onClose])

  // 键盘选中后必须滚进可视区 —— 候选常常超过一屏，且浮层贴着输入框。
  useLayoutEffect(() => {
    const box = listRef.current
    if (!box) return
    const reveal = () => {
      const el = activeRef.current
      if (!el) return
      const row = el.getBoundingClientRect(), list = box.getBoundingClientRect()
      if (row.top < list.top) box.scrollTop += row.top - list.top - 4
      else if (row.bottom > list.bottom) box.scrollTop += row.bottom - list.bottom + 4
    }
    reveal()
    const observer = new ResizeObserver(reveal)
    observer.observe(box)
    return () => observer.disconnect()
  }, [activeIndex, items])

  const rows = useMemo(() => {
    const out: Array<
      | { type: 'header'; key: string; label: string; count: number }
      | { type: 'more'; key: string; kind: CandidateKind; remaining: number }
      | { type: 'item'; key: string; item: ScoredCandidate; index: number }
    > = []
    const kinds = [...new Set(items.map((item) => item.candidate.kind))]
    for (const kind of kinds) {
      const group = items.map((item, index) => ({ item, index })).filter(({ item }) => item.candidate.kind === kind)
      const count = (allItems ?? items).filter((item) => item.candidate.kind === kind).length
      out.push({ type: 'header', key: 'h-' + kind, label: t(GROUP_TITLE_KEYS[kind]), count })
      group.forEach(({ item, index }) => out.push({ type: 'item', key: kind + item.candidate.insert, item, index }))
      if (count > group.length) out.push({ type: 'more', key: 'more-' + kind, kind, remaining: count - group.length })
    }
    return out
  }, [items, allItems, locale])

  if (!items.length) {
    return createPortal(
      <div ref={menuRef} data-trigger-menu style={style} className="z-menu">
        <div className="overflow-hidden rounded-xl border border-line bg-surface shadow-pop">
          <div role="status" className="flex items-center gap-2 px-3 py-2.5">
            {loading ? <Loader2 aria-hidden className="h-3.5 w-3.5 shrink-0 animate-spin text-faint" /> : <TriangleAlert aria-hidden className="h-3.5 w-3.5 shrink-0 text-faint" />}
            <span className="text-xs text-muted">{loading ? statusText : emptyText}</span>
          </div>
          <SearchStatus statusText={loading ? undefined : statusText} loading={loading} sourceRoot={sourceRoot} errorText={errorText} onRetry={onRetry} />
        </div>
      </div>, document.body,
    )
  }

  return createPortal(
      <div ref={menuRef} data-trigger-menu style={{ ...style, overflowY: 'hidden' }} className="z-menu flex flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-pop">
        <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-1.5">
          <span className="text-2xs font-medium uppercase tracking-wide text-faint">{title}</span>
          <span className="text-2xs text-faint">{(allItems ?? items).length}</span>
          <span className="ml-auto hidden items-center gap-1 text-2xs text-faint sm:flex">
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd>
            <span>{t("ui.components.chat.TriggerMenu.c11330b85234")}</span>
            <Kbd className="ml-1">↵</Kbd>
            <span>{t("ui.components.chat.TriggerMenu.4268493cd34f")}</span>
            <Kbd className="ml-1">esc</Kbd>
            <span>{t("ui.components.chat.TriggerMenu.3fd47edce45b")}</span>
          </span>
        </div>

        <div ref={listRef} id={id} role="listbox" aria-label={title} className="min-h-0 max-h-64 overflow-y-auto overscroll-contain p-1">
          {rows.map((row) => {
            if (row.type === 'header') {
              return (
                <div
                  key={row.key}
                  className="flex items-center gap-1.5 px-2 pb-1 pt-2 text-2xs font-medium uppercase tracking-wide text-faint first:pt-1"
                >
                  {row.label}
                  <span className="opacity-60">{row.count}</span>
                </div>
              )
            }
            if (row.type === 'more') return <ShowMoreControl key={row.key}
              onPointerDown={(event) => event.preventDefault()}
              onClick={() => onShowMore?.(row.kind)}
              className="text-xs text-brand" />
            const { candidate } = row.item
            const Icon = KIND_ICONS[candidate.kind]
            const on = row.index === activeIndex
            return (
              <button
                key={row.key}
                ref={on ? activeRef : undefined}
                id={`${id}-${row.index}`}
                role="option"
                aria-selected={on}
                data-completion={candidate.insert}
                tabIndex={-1}
                type="button"
                // Clicking a suggestion must preserve the editor's focus/caret.
                onPointerDown={(event) => event.preventDefault()}
                onPointerMove={() => onActive?.(row.item)}
                onFocus={() => onActive?.(row.item)}
                onClick={() => onPick(row.item)}
                className={cn(
                  'flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors',
                  on ? 'bg-brand/10 text-ink ring-1 ring-inset ring-brand/25' : 'text-muted',
                )}
              >
                <Icon className={cn('h-3.5 w-3.5 shrink-0', on ? 'text-brand' : 'text-faint')} />
                <span className="min-w-0 flex-1 text-xs"><span className="block truncate">
                  {segments(candidate.title, row.item.hits).map((seg, i) => (
                    <span key={i} className={seg.hit ? 'font-semibold text-brand' : undefined}>
                      {seg.text}
                    </span>
                  ))}</span>
                  {candidate.hint && candidate.hint !== candidate.title && (candidate.kind === 'file' || candidate.kind === 'directory') ? <span className="block truncate text-2xs text-faint" title={candidate.hint}>{candidate.hint}</span> : null}
                </span>
                {candidate.hint && candidate.hint !== candidate.title && candidate.kind !== 'file' && candidate.kind !== 'directory' ? (
                  <span className="hidden max-w-shortcut shrink-0 truncate text-2xs text-faint sm:block">
                    {candidate.hint}
                  </span>
                ) : null}
              </button>
            )
          })}
        </div>
        <SearchStatus statusText={statusText} loading={loading} sourceRoot={sourceRoot} errorText={errorText} onRetry={onRetry} />
      </div>, document.body,
  )
})

function SearchStatus({ statusText, loading, sourceRoot, errorText, onRetry }: Pick<TriggerMenuProps, 'statusText' | 'loading' | 'sourceRoot' | 'errorText' | 'onRetry'>) {
  useLocale()

  if (!statusText && !sourceRoot && !errorText) return null
  return <div className="refresh-action-container shrink-0 space-y-1 border-t border-line px-3 py-2 text-2xs">
    {sourceRoot ? <p className="truncate text-faint" title={sourceRoot}>{t("ui.components.chat.TriggerMenu.691f076946a5")}{sourceRoot}</p> : null}
    {errorText ? <div role="alert" className="flex items-start justify-between gap-2"><p className="min-w-0 break-words text-danger">{errorText}</p><button type="button" className="shrink-0 text-brand hover:underline" onMouseDown={(event) => event.preventDefault()} onClick={onRetry}>{t("ui.components.chat.TriggerMenu.cbc730be421c")}</button></div>
      : statusText ? <div className="flex items-start justify-between gap-2"><p role="status" className="min-w-0 text-muted">{statusText}</p>{!loading && onRetry ? <RefreshButton size="xs" label={t("ui.components.chat.TriggerMenu.e005e6e25e18")} onMouseDown={(event) => event.preventDefault()} onClick={onRetry} /> : null}</div> : null}
  </div>
}
