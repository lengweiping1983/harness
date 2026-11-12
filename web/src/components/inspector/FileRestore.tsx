import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { useEffect, useRef, useState } from 'react'
import { RotateCcw } from 'lucide-react'
import type { FileRestorePreview, FileRestoreReceipt, NetChangedFile } from '@/lib/protocol.generated'
import type { ChangesTarget } from '@/lib/turnChanges'
import { applyFileRestore, readRestorePreview, readRestoreReceipt } from '@/lib/fileRestore'
import { ApiError } from '@/lib/http'
import { Button } from '@/components/ui/primitives'
import { DiffView } from '@/components/chat/blocks/DiffView'

/** Reads are cancellable. Mutations are reconciled by durable receipt, never retried implicitly. */
export function FileRestore({ target, file, through }: { target: ChangesTarget; file: NetChangedFile; through: number }) {
  useLocale()

  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState<FileRestorePreview | null>(null)
  const [receipt, setReceipt] = useState<FileRestoreReceipt | null>(null)
  const [requestId, setRequestId] = useState<string | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const [retry, setRetry] = useState(0)
  const [busy, setBusy] = useState(false)
  const heading = useRef<HTMLHeadingElement>(null)
  const alive = useRef(true)
  const submitting = useRef(false)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    const pendingId = requestId && (!receipt || receipt.status === 'unknown') ? requestId : null
    setPreview(null); setError(''); setRequestId(pendingId)
    if (!pendingId) setReceipt(null)
    void readRestorePreview(target, file, through, controller.signal)
      .then(value => { if (!controller.signal.aborted) {
        setPreview(value)
        if (!pendingId) {
          setReceipt(value.latest_receipt)
          if (value.latest_receipt?.status === 'unknown') setRequestId(value.latest_receipt.request_id)
        }
      } })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure instanceof ApiError && failure.failure.copy ? failure.failure.copy : failure.message) })
    return () => controller.abort()
  }, [open, target, file, through, retry])
  useEffect(() => { if (open && (preview || error)) heading.current?.focus({ preventScroll: true }) }, [open, preview, error])
  const confirm = async () => {
    if (!preview?.available || submitting.current || requestId) return
    const id = crypto.randomUUID()
    submitting.current = true; setBusy(true); setRequestId(id); setError('')
    try {
      const value = await applyFileRestore(target, file, preview, id)
      if (alive.current) setReceipt(value)
    } catch (failure) {
      // A missing HTTP response does not mean the filesystem mutation failed.
      try {
        const value = await readRestoreReceipt(target, file, id)
        if (alive.current) setReceipt(value)
      } catch {
        if (alive.current) setError(failure instanceof ApiError && failure.status === 409 ? failure.failure.copy ?? failure.message : {key: 'ui.components.inspector.FileRestore.60925c84eb6b'})
      }
    } finally { submitting.current = false; if (alive.current) setBusy(false) }
  }
  const reconcile = async () => {
    if (!requestId || busy) return
    setBusy(true); setError('')
    try {
      const value = await readRestoreReceipt(target, file, requestId)
      if (alive.current) setReceipt(value)
    } catch (failure) { if (alive.current) setError(failure instanceof ApiError && failure.failure.copy ? failure.failure.copy : (failure as Error).message) }
    finally { if (alive.current) setBusy(false) }
  }
  return <div data-file-restore={file.first_seq} className="space-y-3">
    {!open ? <Button size="xs" variant="ghost" icon={<RotateCcw className="h-3 w-3" />} onClick={() => setOpen(true)}>{t("ui.components.inspector.FileRestore.a7c1cf1bb949")}</Button>
      : <div className="space-y-3 rounded-lg border border-warn/30 bg-warn/5 p-3">
        <h4 ref={heading} tabIndex={-1} className="text-xs font-medium">{t('ui.fileRestore.heading', {turn: target.turn})}</h4>
        <p className="text-xs leading-5 text-muted">{t("ui.components.inspector.FileRestore.2bb4d9af8c01")}</p>
        {busy ? <p role="status" className="text-xs text-muted">{t("ui.components.inspector.FileRestore.a732782f194e")}</p> : null}
        {error ? <p role="alert" className="break-words text-xs text-danger">{noticeText(error)}</p> : null}
        {receipt ? <p role="status" className={'text-xs leading-5 ' + (receipt.status === 'restored' ? 'text-ok' : 'text-warn')}>{t("ui.components.inspector.FileRestore.381041c93239")}{receipt.message}</p> : null}
        {!preview && !error ? <p role="status" className="text-xs text-muted">{t("ui.components.inspector.FileRestore.4c1dec2ba51e")}</p> : preview ? <>
          {!preview.available ? <p className="text-xs leading-5 text-warn">{preview.reason}</p> : <>
            <p className="text-xs leading-5 text-ink">{preview.action === 'remove' ? t("ui.components.inspector.FileRestore.46dbed906851") : t("ui.components.inspector.FileRestore.6e3a85e3039d")}</p>
            {preview.truncated ? <p className="text-xs text-warn">{t("ui.components.inspector.FileRestore.90525845bc55")}</p> : null}
            {preview.patch ? <DiffView output={preview.patch} display={{ diff: preview.patch }} path={preview.path} /> : <p className="text-xs text-muted">{preview.action === 'remove' ? t("ui.components.inspector.FileRestore.c2353ae10991") : t("ui.components.inspector.FileRestore.a652ac3013b6")}</p>}
          </>}
        </> : null}
        <div className="flex flex-wrap gap-2">
          {preview?.available && !requestId ? <Button size="xs" variant="danger" disabled={busy} onClick={() => void confirm()}>{preview.action === 'remove' ? t("ui.components.inspector.FileRestore.ff9493161110") : t("ui.components.inspector.FileRestore.4273639151bb")}</Button> : null}
          {requestId && (!receipt || receipt.status === 'unknown') ? <Button size="xs" variant="secondary" disabled={busy} onClick={() => void reconcile()}>{t("ui.components.inspector.FileRestore.b1edad815670")}</Button> : null}
          <Button size="xs" variant="secondary" disabled={busy} onClick={() => setRetry(value => value + 1)}>{t("ui.components.inspector.FileRestore.6edab17576b4")}</Button>
          <Button size="xs" variant="ghost" disabled={busy} onClick={() => setOpen(false)}>{t("ui.components.inspector.FileRestore.03fb52363419")}</Button>
        </div>
      </div>}
  </div>
}
