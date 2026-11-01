import { t, useLocale } from '@/i18n'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AtSign, ChevronRight, ListChecks, Paperclip, Plug, Plus, Target, Sparkles, Users, X } from 'lucide-react'
import type {ComposerMode} from '@/lib/composerMode'
import { useAppStore } from '@/store/app'
import { useAnchoredMenu } from '@/hooks/useAnchoredMenu'
import { CapabilityPicker } from './CapabilityPicker'

/** 常驻加号收纳上下文和能力入口，选择标签留在输入框工具条。 */
export function ComposerAddMenu({ sessionId, draft, onChange, focusEditor, onAddFiles, onReference, mode, modeBusy, onSelectMode }: {
  sessionId: string | null; draft: string; onChange: (text: string) => void; focusEditor: () => void
  onAddFiles: () => void; onReference: (prefix: '@') => void
  mode: ComposerMode; modeBusy: boolean; onSelectMode: (mode: ComposerMode) => void
}) {
  useLocale()
  const [open, setOpen] = useState(false)
  const [agentOpen, setAgentOpen] = useState(false)
  const [skillOpen, setSkillOpen] = useState(false)
  const showAgents = useCallback((next: boolean) => { if (next) setSkillOpen(false); setAgentOpen(next) }, [])
  const showSkills = useCallback((next: boolean) => { if (next) setAgentOpen(false); setSkillOpen(next) }, [])
  const anchor = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const style = useAnchoredMenu(anchor, open, 248)
  useEffect(() => { setOpen(false); setAgentOpen(false); setSkillOpen(false) }, [sessionId])
  useEffect(() => {
    if (!open) return
    const frame = requestAnimationFrame(() => menu.current?.querySelector<HTMLButtonElement>('button')?.focus())
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !menu.current?.contains(event.target) && !anchor.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', dismiss, true)
    return () => { cancelAnimationFrame(frame); document.removeEventListener('pointerdown', dismiss, true) }
  }, [open])
  function action(run: () => void) { setOpen(false); run() }
  const rows = [
    { icon: Paperclip, key: 'files', run: onAddFiles },
    { icon: AtSign, key: 'reference', run: () => onReference('@') },
    { icon: ListChecks, key: 'plan', run: () => onSelectMode('plan'), disabled: modeBusy, selected: mode === 'plan' },
    { icon: Target, key: 'goal', run: () => onSelectMode('goal'), disabled: modeBusy, selected: mode === 'goal' },
    { icon: Users, key: 'agents', run: () => { setSkillOpen(false); setAgentOpen(true) } },
    { icon: Sparkles, key: 'skills', run: () => { setAgentOpen(false); setSkillOpen(true) } },
    { icon: Plug, key: 'connectors', run: () => useAppStore.getState().setSettingsOpen(true, 'connectors') },
  ]
  return <>
    <button ref={anchor} type="button" aria-label={t('ui.composerAdd.open')} aria-haspopup="menu" aria-expanded={open}
      onClick={() => { setAgentOpen(false); setSkillOpen(false); setOpen(value => !value) }}
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted hover:bg-raised hover:text-ink focus-visible:outline-brand">
      {open ? <X className="h-4 w-4" /> : <Plus className="h-5 w-5" />}
    </button>
    <CapabilityPicker capabilityKind="agents" sessionId={sessionId} draft={draft} onChange={onChange} focusEditor={focusEditor} open={agentOpen} setOpen={showAgents} menuAnchor={anchor} />
    <CapabilityPicker capabilityKind="skills" sessionId={sessionId} draft={draft} onChange={onChange} focusEditor={focusEditor} open={skillOpen} setOpen={showSkills} menuAnchor={anchor} />
    {open ? createPortal(<div ref={menu} role="menu" aria-label={t('ui.composerAdd.open')} style={style}
      className="z-menu rounded-xl border border-line bg-surface p-1.5 shadow-pop"
      onKeyDown={event => {
        if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setOpen(false); anchor.current?.focus() }
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
          event.preventDefault()
          const buttons = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])
          const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
          buttons[next]?.focus()
        }
      }}>
      {rows.map(({ icon: Icon, key, run, disabled, selected }) => <button key={key} type="button" role="menuitem" aria-current={selected ? 'true' : undefined} disabled={disabled}
        onPointerEnter={event => event.currentTarget.focus()}
        onClick={() => action(run)} className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm text-ink hover:bg-raised focus-visible:bg-raised focus:outline-none disabled:opacity-50">
        <Icon className="h-4 w-4 text-muted" /><span className="flex-1">{t('ui.composerAdd.' + key)}</span><ChevronRight className="h-3.5 w-3.5 text-faint" />
      </button>)}
    </div>, document.body) : null}
  </>
}
