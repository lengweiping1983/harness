import { noticeText } from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { Button, Overlay } from '@/components/ui/primitives'
import { nameSaveKey, useAppStore } from '@/store/app'
import type { ApiFailure } from '@/lib/api'

export interface RenameTarget { kind: 'session' | 'workspace'; id: string; value: string }

export function RenameDialog({ target, onClose }: { target: RenameTarget; onClose: () => void }) {
  useLocale()

  const [draft, setDraft] = useState(target.value)
  const [error, setError] = useState<ApiFailure | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const submitting = useRef(false)
  const alive = useRef(true)
  const busy = useAppStore((state) => Boolean(state.nameSaves[nameSaveKey(target.kind, target.id)]))
  const heading = useId()
  const feedback = useId()
  const title = target.kind === 'session' ? t("ui.components.overlays.RenameDialog.5f64610f9fec") : t("ui.components.overlays.RenameDialog.84ba75cf3f85")
  useLayoutEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => { input.current?.focus(); input.current?.select() }, [])
  useEffect(() => { if (error && !busy) input.current?.focus() }, [error, busy])
  const close = () => { if (!submitting.current && !busy) onClose() }
  const save = async () => {
    if (submitting.current || busy) return
    submitting.current = true
    setError(null)
    const state = useAppStore.getState()
    const failure = target.kind === 'session' ? await state.renameSession(target.id, draft) : await state.renameWorkspace(target.id, draft)
    submitting.current = false
    if (!alive.current) return
    if (failure) setError(failure)
    else onClose()
  }
  return (
    <Overlay onClose={close} labelledBy={heading} className="max-w-md mt-dialog">
      <form className="space-y-4 p-5" onSubmit={(event) => { event.preventDefault(); void save() }}>
        <h2 id={heading} className="text-base font-semibold text-ink">{title}</h2>
        <label className="block space-y-2 text-sm text-muted">
          <span>{t("ui.components.overlays.RenameDialog.d44e9b3d3b31")}</span>
          <input ref={input} autoFocus value={draft} disabled={busy} aria-invalid={Boolean(error)} aria-describedby={feedback}
            onChange={(event) => { setDraft(event.target.value); setError(null) }}
            onKeyDown={(event) => { if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.keyCode === 229)) event.preventDefault() }}
            className="w-full rounded-lg border border-line bg-raised px-3 py-2 text-sm text-ink focus:border-brand focus:outline-none disabled:opacity-60" />
        </label>
        <p id={feedback} role={error ? 'alert' : 'status'} className={error ? 'text-sm text-danger' : 'text-xs text-muted'}>
          {error ? noticeText(error.copy ?? error.message) : (busy ? t("ui.components.overlays.RenameDialog.f039105a5dc4") : t("ui.components.overlays.RenameDialog.199475d28491"))}
        </p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" disabled={busy} onClick={close}>{t("ui.components.overlays.RenameDialog.2cd0f3be8738")}</Button>
          <Button type="submit" loading={busy} disabled={!draft.trim()}>{busy ? t("ui.components.overlays.RenameDialog.ff509c9ba052") : error ? t("ui.components.overlays.RenameDialog.917e75040547") : t("ui.components.overlays.RenameDialog.a3030bf8f16d")}</Button>
        </div>
      </form>
    </Overlay>
  )
}
