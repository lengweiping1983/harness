import {useEffect, useId, useLayoutEffect, useRef, useState} from 'react'
import {createPortal, flushSync} from 'react-dom'
import {Check, ChevronDown, GitBranch} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {useAnchoredMenu} from '@/hooks/useAnchoredMenu'
import {usePageVisible} from '@/hooks/usePageVisible'
import type {WorkspaceOptions} from '@/lib/protocol.generated'
import {cn} from '@/lib/utils'

type BranchOptions = {options: WorkspaceOptions; selected: string; isolated: boolean; onChange: (ref: string) => void}

/** 检出菜单共用本地与隔离分支范围，保留当前选项和完整的原始名称。 */
export function CheckoutBranchOptions({options, selected, isolated, onChange}: BranchOptions) {
  useLocale()
  const refs = [...new Set([selected, ...(isolated ? options.refs : options.local_branches)])]
  return <>{refs.map(ref => <button key={ref} type="button" role="menuitemradio" aria-checked={selected === ref} tabIndex={-1}
    onClick={() => onChange(ref)} className={cn('flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left text-compact hover:bg-raised focus-visible:bg-raised',
      selected === ref ? 'bg-brand/10 text-brand' : 'text-ink')}>
    <GitBranch aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" />
    <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{ref}</span>
    {selected === ref ? <Check aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : null}
  </button>)}</>
}

/** 使用主题浮层替代系统下拉框；选择只更新检出草稿。 */
export function CheckoutBranchMenu({options, selected, isolated, compact, disabled, onChange}: BranchOptions & {compact: boolean; disabled: boolean}) {
  useLocale()
  const visible = usePageVisible()
  const [open, setOpen] = useState(false)
  const anchor = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const id = useId()
  const shown = open && visible && !disabled
  const style = useAnchoredMenu(anchor, shown, 256)
  const label = t(isolated ? 'ui.newConversation.baseBranch' : 'ui.newConversation.branch')
  const close = () => {
    flushSync(() => setOpen(false))
    anchor.current?.focus({preventScroll: true})
  }

  useEffect(() => {if (!visible || disabled) setOpen(false)}, [visible, disabled])
  useLayoutEffect(() => {
    if (!shown || style.visibility !== 'visible') return
    menu.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus({preventScroll: true})
  }, [shown, style.visibility])
  useEffect(() => {
    if (!shown) return
    const dismiss = (event: Event) => {
      if (!menu.current?.contains(event.target as Node) && !anchor.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('focusin', dismiss)
    // 先于外层对话框的 document 捕获处理，只接管当前菜单所属的按键。
    const keyDown = (event: KeyboardEvent) => {
      if (!menu.current?.contains(event.target as Node)) return
      if (event.isComposing || event.keyCode === 229) {
        if (event.key === 'Enter' || event.key === ' ') event.preventDefault()
        return
      }
      if (event.key === 'Escape' || event.key === 'Tab') {
        if (event.key === 'Escape') event.preventDefault()
        event.stopPropagation(); close(); return
      }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault(); event.stopPropagation()
        const items = Array.from(menu.current.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]'))
        const index = items.indexOf(document.activeElement as HTMLButtonElement)
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
          : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
        items[next]?.focus()
      }
    }
    window.addEventListener('keydown', keyDown, true)
    return () => {
      document.removeEventListener('pointerdown', dismiss); document.removeEventListener('focusin', dismiss)
      window.removeEventListener('keydown', keyDown, true)
    }
  }, [shown])

  return <>
    <button ref={anchor} type="button" aria-label={label} title={selected} aria-haspopup="menu" aria-expanded={shown} aria-controls={shown ? id : undefined} disabled={disabled}
      onClick={() => setOpen(current => !current)}
      onKeyDown={event => {
        if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) {
          if (event.key === 'Enter' || event.key === ' ') event.preventDefault()
          return
        }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {event.preventDefault(); event.stopPropagation(); setOpen(true)}
      }}
      className={cn('inline-flex min-w-0 max-w-full items-center gap-1.5 text-xs hover:bg-raised disabled:opacity-55',
        compact ? 'h-6 rounded-md px-2' : 'h-8 rounded-lg border border-line bg-surface px-2.5', shown ? 'bg-brand/10 text-brand' : 'text-muted hover:text-ink')}>
      <GitBranch aria-hidden className="h-3.5 w-3.5 shrink-0" />
      <span className="max-w-path truncate text-ink">{selected}</span>
      <ChevronDown aria-hidden className={cn('h-3 w-3 shrink-0 transition-transform', shown && 'rotate-180')} />
    </button>
    {shown ? createPortal(<div id={id} ref={menu} role="menu" aria-label={label} style={style}
      className="z-context-menu max-w-viewport rounded-xl border border-line bg-surface p-1 shadow-menu">
      <p className="px-2.5 py-1.5 text-xs text-muted">{label}</p>
      <div className="max-h-64 overflow-y-auto overscroll-contain">
        <CheckoutBranchOptions options={options} selected={selected} isolated={isolated} onChange={ref => {close(); if (ref !== selected) onChange(ref)}} />
      </div>
    </div>, document.body) : null}
  </>
}
