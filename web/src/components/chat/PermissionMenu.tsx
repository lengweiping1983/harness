import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal, flushSync } from 'react-dom'
import { Check, ChevronDown, Hand, Loader2, ShieldCheck, ShieldQuestion, Unlock } from 'lucide-react'
import { cn } from '@/lib/utils'
import { permissionProfile, permissionProfiles } from '@/lib/permissions'
import { usePermissionStore } from '@/store/permissions'
import { useAppStore } from '@/store/app'
import { useAnchoredMenu } from '@/hooks/useAnchoredMenu'
import { feedbackScroll } from '@/lib/revealFeedback'

export function PermissionMenu({ connection }: { connection: string }) {
  useLocale()

  const sessionId = useAppStore((state) => state.activeSessionId)
  const { policy, scope, busy, loading, inherited, readError, saveError, saveUnconfirmed, dismissSaveError, load, save } = usePermissionStore()
  const parentControlled = Boolean(sessionId && scope && scope !== sessionId)
  const [open, setOpen] = useState(false)
  const generation = useRef(0)
  useLayoutEffect(() => {
    generation.current++
    return () => {generation.current++}
  }, [sessionId, open])
  const anchor = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const focused = useRef(false)
  const readFeedback = useRef<HTMLDivElement>(null)
  const saveFeedback = useRef<HTMLDivElement>(null)
  const seenFeedback = useRef({ read: null as ResourceText | null, save: null as ResourceText | null })
  const style = useAnchoredMenu(anchor, open)
  const selected = policy ? permissionProfile(policy) : null
  const label = permissionProfiles.find((profile) => profile.id === selected)?.label
    ?? (selected === 'read_only' ? t("ui.components.chat.PermissionMenu.c0702d24132b") : policy ? t("ui.components.chat.PermissionMenu.753681c48f08") : t("ui.components.chat.PermissionMenu.978cbca6265d"))
  const Icon = selected === 'full_access' ? Unlock : selected === 'auto_review' ? ShieldCheck : Hand

  useEffect(() => { setOpen(false); if (connection === 'open') void load(sessionId) }, [connection, load, sessionId])
  useEffect(() => {
    if (!open) { focused.current = false; return }
    if (style.visibility !== 'visible') return
    const first = (readError ? readFeedback.current : saveError ? saveFeedback.current : null)?.querySelector<HTMLButtonElement>('button:not(:disabled)')
      ?? menu.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]:not(:disabled)')
      ?? menu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')
    if (!focused.current && !loading && !busy && first) { first.focus(); focused.current = true }
    const dismiss = (event: PointerEvent) => {
      if (!menu.current?.contains(event.target as Node) && !anchor.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', dismiss)
    const blur = (event: FocusEvent) => {
      if (!menu.current?.contains(event.target as Node) && !anchor.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('focusin', blur)
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('focusin', blur) }
  }, [open, style.visibility, loading, busy, readError, saveError])
  useEffect(() => {
    if (!open) { seenFeedback.current = { read: null, save: null }; return }
    if (style.visibility !== 'visible') return
    const target = saveError && saveError !== seenFeedback.current.save ? saveFeedback.current
      : readError && readError !== seenFeedback.current.read ? readFeedback.current : null
    seenFeedback.current = { read: readError, save: saveError }
    if (target && menu.current) {
      const frame = menu.current.getBoundingClientRect(), bounds = target.getBoundingClientRect()
      menu.current.scrollTop = feedbackScroll(menu.current.scrollTop, frame.top, frame.bottom, bounds.top, bounds.bottom)
    }
  }, [open, style.visibility, readError, saveError])

  const choose = async (profile: Parameters<typeof save>[0]) => {
    const ticket = generation.current
    const saved = await save(profile)
    if (saved && generation.current === ticket && useAppStore.getState().activeSessionId === sessionId) {
      setOpen(false)
      anchor.current?.focus()
    }
  }

  return <>
    <button ref={anchor} type="button" aria-label={t("ui.components.chat.PermissionMenu.e7a50098114c", {p0: (label)})} aria-haspopup="menu" aria-expanded={open}
      onClick={() => { setOpen(!open); if (!open) void load(sessionId) }}
      className={cn('inline-flex h-8 items-center gap-1.5 rounded-xl px-2.5 text-xs font-medium hover:bg-raised',
        selected === 'full_access' ? 'text-warn' : 'text-muted hover:text-ink')}>
      <Icon className="h-3.5 w-3.5" /><span>{label}</span><ChevronDown className="h-3 w-3" />
    </button>
    {open ? createPortal(<div ref={menu} style={style} className="z-popover max-w-viewport rounded-2xl border border-line bg-surface p-2 shadow-xl"
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
        if (event.key === 'Escape') { event.preventDefault(); setOpen(false); anchor.current?.focus() }
        if (event.key === 'Tab') {
          const buttons = Array.from(menu.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [tabindex="0"]') ?? [])
          if ((event.target as HTMLElement).closest('[role="menuitemradio"]')
            || document.activeElement === (event.shiftKey ? buttons[0] : buttons.at(-1))) {
            // Remove the portal before the browser performs its native Tab
            // step, so it cannot navigate back into stale popup controls.
            flushSync(() => setOpen(false))
            anchor.current?.focus()
          }
        }
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) && (event.target as HTMLElement).closest('[role="menu"]')) {
          event.preventDefault()
          const items = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]:not(:disabled)') ?? [])
          if (!items.length) return
          const index = items.indexOf(document.activeElement as HTMLButtonElement)
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
            : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
          items[next]?.focus()
        }
      }}>
      <p className="px-2 py-1.5 text-xs text-muted">{t("ui.components.chat.PermissionMenu.9fd69f267785")}</p>
      <div role="menu" aria-label={t("ui.components.chat.PermissionMenu.a504543d35af")}>
        {permissionProfiles.map((profile) => {
          const RowIcon = profile.icon === 'hand' ? Hand : profile.icon === 'shield' ? ShieldCheck : Unlock
          return <button key={profile.id} type="button" role="menuitemradio" aria-checked={selected === profile.id}
            disabled={parentControlled || busy || loading || connection !== 'open'} onClick={() => void choose(profile.id)} className={cn('flex w-full items-center gap-3 rounded-xl px-2 py-2.5 text-left hover:bg-raised focus-visible:bg-raised focus-visible:outline-none disabled:opacity-50',
              selected === profile.id && 'bg-raised', profile.id === 'full_access' && 'text-warn')}>
            <RowIcon className="h-4 w-4 shrink-0" />
            <span className="min-w-0 flex-1"><span className="block text-sm font-medium">{profile.label}</span>
              <span className="mt-0.5 block text-xs text-muted">{profile.description}</span></span>
            {selected === profile.id ? <Check className="h-4 w-4 shrink-0" /> : null}
          </button>
        })}
      </div>
      {sessionId && !parentControlled ? <button type="button" disabled={inherited || busy || loading} onClick={() => void choose(null)} className="mt-1 w-full rounded-lg px-2 py-1.5 text-left text-xs text-muted hover:bg-raised disabled:opacity-50">{inherited ? t("ui.components.chat.PermissionMenu.19842acee472") : t("ui.components.chat.PermissionMenu.13c23e70777f")}</button> : null}
      {sessionId ? <button type="button" onClick={() => { setOpen(false); useAppStore.getState().setInspectorTab('runtime') }} className="w-full rounded-lg px-2 py-1.5 text-left text-xs text-brand hover:bg-raised">{t("ui.components.chat.PermissionMenu.3ee28e0bf5a0")}</button> : null}
      {parentControlled ? <p className="px-2 py-2 text-xs text-muted">{t("ui.components.chat.PermissionMenu.1debdc254a70")}</p> : null}
      <p className="flex gap-1.5 border-t border-line px-2 pt-2 text-xs leading-relaxed text-muted"><ShieldQuestion className="mt-0.5 h-3.5 w-3.5 shrink-0" />{sessionId ? t("ui.components.chat.PermissionMenu.e82a54221dce") : t("ui.components.chat.PermissionMenu.44fee0e9f6f8")}{t("ui.components.chat.PermissionMenu.38435c47a7dc")}</p>
      {loading ? <p role="status" className="px-2 pt-2 text-xs">{t("ui.components.chat.PermissionMenu.352a3cf9770d")}</p> : null}
      {busy ? <p role="status" className="flex items-center gap-1 px-2 pt-2 text-xs"><Loader2 className="h-3 w-3 animate-spin" />{t("ui.components.chat.PermissionMenu.6718df996064")}</p> : null}
      {readError ? <div ref={readFeedback} role="alert" aria-label={t("ui.components.chat.PermissionMenu.bf30090821dd")} className="space-y-1 px-2 pt-2 text-xs text-danger">
        <p tabIndex={0} className="max-h-24 overflow-auto [overflow-wrap:anywhere]">{t("ui.components.chat.PermissionMenu.62a03d787939", {detail: noticeText(readError)})}</p>
        <button type="button" disabled={loading || busy || connection !== 'open'} onClick={() => void load(sessionId)} className="underline disabled:opacity-50">{t("ui.components.chat.PermissionMenu.dce8a493b146")}</button>
      </div> : null}
      {saveError ? <div ref={saveFeedback} role="alert" aria-label={t("ui.components.chat.PermissionMenu.77fa12bffe9b")} className="space-y-1 px-2 pt-2 text-xs text-danger">
        <p tabIndex={0} className="max-h-24 overflow-auto [overflow-wrap:anywhere]">{t(saveUnconfirmed ? "ui.components.chat.PermissionMenu.375506c3c436" : "ui.components.chat.PermissionMenu.3584fa6c0890", {detail: noticeText(saveError)})}</p>
        {saveUnconfirmed ? <p className="text-muted">{t("ui.components.chat.PermissionMenu.e0ab38389b76")}</p> : null}
        <div className="flex flex-wrap gap-3">
          <button type="button" disabled={loading || busy || connection !== 'open'} onClick={() => void load(sessionId)} className="underline disabled:opacity-50">{t("ui.components.chat.PermissionMenu.d9a9ac4135b5")}</button>
          <button type="button" onClick={dismissSaveError} className="underline">{t("ui.components.chat.PermissionMenu.3e4ea1cef4c8")}</button>
        </div>
      </div> : null}
    </div>, document.body) : null}
  </>
}
