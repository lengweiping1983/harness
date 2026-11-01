import {useEffect, useRef, useState} from 'react'
import {t, useLocale} from '@/i18n'
import {noticeText} from '@/lib/noticeText'
import {cn} from '@/lib/utils'
import {useAppStore, type Toast} from '@/store/app'
import {createToastTimer, toastDuration} from '@/lib/toastTimer'

/* ============================ Toast ============================ */

const TOAST_TONE: Record<string, string> = {
  info: 'border-line bg-surface',
  success: 'border-ok/30 bg-ok/10',
  warn: 'border-warn/30 bg-warn/10',
  danger: 'border-danger/30 bg-danger/10',
}

function ToastItem({ toast, paused, dismiss }: { toast: Toast; paused: boolean; dismiss: (id: string) => void }) {
  useLocale()

  const timerRef = useRef<ReturnType<typeof createToastTimer> | null>(null)
  const duration = toastDuration(toast)
  const title = noticeText(toast.title)
  useEffect(() => {
    if (toast.persistent) return
    const timer = createToastTimer(duration, () => dismiss(toast.id))
    timerRef.current = timer
    return () => { timer.cancel(); timerRef.current = null }
  }, [toast.id, duration, toast.persistent, dismiss])
  useEffect(() => {
    if (paused) timerRef.current?.pause()
    else timerRef.current?.resume()
  }, [paused, toast.id, duration, toast.persistent])
  return (
    <div className={cn('shrink-0 animate-slide-up rounded-lg border px-3 py-2 shadow-pop backdrop-blur [overflow-wrap:anywhere]', TOAST_TONE[toast.tone] ?? TOAST_TONE.info)}>
      <div className="flex items-start gap-2">
        <p role={toast.tone === 'danger' ? 'alert' : 'status'} tabIndex={title.length > 100 ? 0 : undefined} className="max-h-16 min-w-0 flex-1 overflow-y-auto text-xs font-medium text-ink">{title}</p>
        <button type="button" onClick={() => dismiss(toast.id)} className="flex h-8 w-8 shrink-0 items-center justify-center rounded text-faint transition-colors hover:bg-raised hover:text-ink" aria-label={t("ui.components.overlays.CommandPalette.58ce29e73319", {p0: title})}>×</button>
      </div>
      {toast.description ? <p tabIndex={0} aria-label={t("ui.components.overlays.CommandPalette.483fe390159f")} className="mt-0.5 max-h-24 overflow-y-auto overscroll-contain text-2xs leading-relaxed text-muted">{noticeText(toast.description)}</p> : null}
      {toast.action ? <div className="mt-2 flex justify-end">
        <button type="button" onClick={() => { toast.action?.run(); dismiss(toast.id) }} className="max-w-full rounded px-1.5 py-1 text-2xs font-medium text-brand hover:bg-brand/10">{noticeText(toast.action.label)}</button>
      </div> : null}
    </div>
  )
}

export function Toaster() {
  useLocale()

  const toasts = useAppStore((s) => s.toasts)
  const dismiss = useAppStore((s) => s.dismissToast)
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const [hidden, setHidden] = useState(document.hidden)
  const regionRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (focused && !regionRef.current?.contains(document.activeElement)) setFocused(false)
    if (!toasts.length) setHovered(false)
  }, [toasts, focused])
  useEffect(() => {
    const changed = () => setHidden(document.hidden)
    document.addEventListener('visibilitychange', changed)
    return () => document.removeEventListener('visibilitychange', changed)
  }, [])
  if (!toasts.length) return null
  return (
    <div ref={regionRef} role="region" aria-label={t("ui.components.overlays.CommandPalette.4b6ded8a7f18")} tabIndex={0} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
      onFocusCapture={() => setFocused(true)} onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false) }}
      className="fixed bottom-4 right-4 z-notifications flex max-h-notifications w-80 max-w-viewport-spaced flex-col gap-2 overflow-y-auto overscroll-contain">
      {toasts.map((toast) => <ToastItem key={toast.id} toast={toast} paused={hovered || focused || hidden} dismiss={dismiss} />)}
    </div>
  )
}
