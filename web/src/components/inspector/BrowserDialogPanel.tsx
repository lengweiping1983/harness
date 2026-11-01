import { t, useLocale } from '@/i18n'
import { useState } from 'react'
import { MessageSquare, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/primitives'
import { browserActionLabel, type BrowserDialog } from '@/lib/browser'
import { noticeText } from '@/lib/noticeText'

/** Keyed by native identity: a chained prompt must never reuse the prior answer. */
export function BrowserDialogPanel({ dialog, control, pending, onRespond }: {
  dialog: BrowserDialog; control: boolean; pending: boolean
  onRespond: (accept: boolean, text?: string) => void
}) {
  useLocale()

  const [text, setText] = useState(dialog.default_value || '')
  const disabled = !control || pending || dialog.response_unconfirmed
  const title = { alert: t("ui.components.inspector.BrowserDialogPanel.f078f281ac64"), confirm: t("ui.components.inspector.BrowserDialogPanel.0da5c971e315"), prompt: t("ui.components.inspector.BrowserDialogPanel.00529eb907a9"), beforeunload: t("ui.components.inspector.BrowserDialogPanel.a44102f968c5") }[dialog.type]
  return <section aria-label={t("ui.components.inspector.BrowserDialogPanel.ceb85a19e9e0")} className="min-w-0 rounded-xl border border-warn/40 bg-warn/5 p-3">
    <div className="flex items-center gap-2 text-xs font-medium"><MessageSquare className="h-3.5 w-3.5 shrink-0 text-warn" /><h3>{title}</h3><span className="ml-auto text-xs font-normal text-muted">{t("ui.components.inspector.BrowserDialogPanel.29e0f143574d")}</span></div>
    <p className="mt-2 break-all text-xs text-muted">{dialog.url}</p>
    {dialog.origin_action ? <p className="mt-1 text-xs text-muted">{t("ui.components.inspector.BrowserDialogPanel.90d699b71e11")}{dialog.origin_action.actor === 'agent' ? 'Agent' : t("ui.components.inspector.BrowserDialogPanel.a0c7716669b5")} · {browserActionLabel[dialog.origin_action.action] ?? dialog.origin_action.action}</p> : null}
    <blockquote className="my-3 max-h-36 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-surface p-3 text-xs leading-relaxed">{dialog.message_copy ? noticeText(dialog.message_copy) : dialog.message || t("ui.components.inspector.BrowserDialogPanel.130621d7d7c2")}</blockquote>
    <p className="text-xs leading-relaxed text-muted">{t("ui.components.inspector.BrowserDialogPanel.e7d2d1b0b0e2")}{control ? t("ui.components.inspector.BrowserDialogPanel.872ba3d31c76") : t("ui.components.inspector.BrowserDialogPanel.74ad70353e6c")}</p>
    {dialog.response_unconfirmed ? <p role="alert" className="mt-2 text-xs text-danger">{t("ui.components.inspector.BrowserDialogPanel.faf0728ba4e4")}</p> : <form className="mt-3 space-y-2" onSubmit={e => { e.preventDefault(); if (!disabled) onRespond(true, dialog.type === 'prompt' ? text : undefined) }}>
      {dialog.type === 'prompt' ? <input aria-label={t("ui.components.inspector.BrowserDialogPanel.041da00331bb")} autoComplete="off" maxLength={2000} disabled={disabled} value={text} onChange={e => setText(e.target.value)} placeholder={t("ui.components.inspector.BrowserDialogPanel.ee73dc55932a")} className="w-full min-w-0 rounded-lg border border-line bg-surface px-3 py-2 text-xs outline-none focus:border-brand" /> : null}
      <div className="flex justify-end gap-2">
        {dialog.type !== 'alert' ? <Button size="xs" variant="secondary" disabled={disabled} onClick={() => onRespond(false)}>{dialog.type === 'beforeunload' ? t("ui.components.inspector.BrowserDialogPanel.276e56649597") : t("ui.components.inspector.BrowserDialogPanel.8b45d2ae7fdf")}</Button> : null}
        <Button size="xs" variant="primary" type="submit" disabled={disabled}>{pending ? <Loader2 className="h-3 w-3 animate-spin" /> : null}{dialog.type === 'alert' ? t("ui.components.inspector.BrowserDialogPanel.de32e20193ad") : dialog.type === 'beforeunload' ? t("ui.components.inspector.BrowserDialogPanel.1bdd7459c6d3") : t("ui.components.inspector.BrowserDialogPanel.72af0ca0c8c2")}</Button>
      </div>
    </form>}
  </section>
}
