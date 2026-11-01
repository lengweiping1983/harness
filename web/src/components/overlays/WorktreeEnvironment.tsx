import { ResourceError, failureText } from '@/lib/resourceError'
import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { useEffect, useRef, useState } from 'react'
import { FolderInput } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { runtimeRequest, ApiError } from '@/lib/http'
import { Button, RefreshButton, ShowMoreControl } from '@/components/ui/primitives'
import { environmentReceiptConfirms, environmentRejectedBeforeWrite, readEnvironmentPending, saveEnvironmentPending, type EnvironmentReceipt } from '@/lib/environmentImport'
import { usePageVisible } from '@/hooks/usePageVisible'

type Preview = {
  configured: boolean; fingerprint: string; copy_count: number; skip_count: number
  total_bytes: number; offset: number; next_offset: number | null
  entries: Array<{path: string; action: 'copy' | 'skip'; size?: number; reason?: string}>
}

export function WorktreeEnvironment({ id, sessionId, disabled = false, defaultOpen = false, active = true }: { id: string; sessionId: string; disabled?: boolean; defaultOpen?: boolean; active?: boolean }) {
  useLocale()
  const visible = usePageVisible() && active
  const [open, setOpen] = useState(defaultOpen)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [receipt, setReceipt] = useState<EnvironmentReceipt | null>(null)
  const [pending, setPending] = useState(() => readEnvironmentPending(sessionId, id))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const generation = useRef(0)
  const reading = useRef<AbortController>()
  const readRevision = useRef(0)
  const writing = useRef(false)
  const base = `/sessions/${encodeURIComponent(sessionId)}/worktrees/${encodeURIComponent(id)}/environment`
  useEffect(() => () => { generation.current++; reading.current?.abort() }, [base])
  useEffect(() => {
    if (open && visible) void load()
    else if (!writing.current) setBusy(false)
    return () => {readRevision.current++; reading.current?.abort()}
  }, [base, open, visible])
  const mark = (value: string | null) => { saveEnvironmentPending(sessionId, id, value); setPending(value) }
  async function load(offset = 0) {
    if (!visible || writing.current) return
    reading.current?.abort()
    const controller = reading.current = new AbortController()
    const version = ++readRevision.current, owner = generation.current
    setBusy(true); setError('')
    const results = await Promise.allSettled([
      runtimeRequest<Preview>(base + '?offset=' + offset, 'GET', undefined, {signal: controller.signal}), runtimeRequest<{receipt: EnvironmentReceipt | null}>(base + '/receipt', 'GET', undefined, {signal: controller.signal}),
    ])
    if (version !== readRevision.current || owner !== generation.current || controller.signal.aborted) return
    const [selection, saved] = results
    if (saved.status === 'fulfilled') {
      if (saved.value.receipt && saved.value.receipt.worktree_id !== id) {
        setReceipt(null); setError({key: 'ui.environment.unconfirmed'})
      } else {
        setReceipt(saved.value.receipt)
        if (environmentReceiptConfirms(pending, saved.value.receipt, id)) mark(null)
      }
    } else setError(failureText(saved.reason))
    if (selection.status === 'fulfilled') {
      if (offset && preview && preview.fingerprint !== selection.value.fingerprint) {
        setPreview(null); setError({key: 'ui.environment.changed'})
      } else setPreview(offset && preview ? {...selection.value, entries: [...preview.entries, ...selection.value.entries]} : selection.value)
    } else { setPreview(null); setError(failureText(selection.reason)) }
    setBusy(false)
  }
  async function copy() {
    if (!preview || pending || busy || disabled) return
    const version = ++generation.current
    writing.current = true
    const fingerprint = preview.fingerprint
    mark(fingerprint); setBusy(true); setError(''); setPreview(null)
    try {
      const result = await runtimeRequest<{receipt: EnvironmentReceipt}>(base, 'POST', {expected_fingerprint: fingerprint})
      // 保存回执判断不依赖组件是否仍显示；切换对话后也不能遗留假待核实标记。
      if (!environmentReceiptConfirms(fingerprint, result.receipt, id)) throw new ResourceError({key: 'ui.environment.unconfirmed'})
      saveEnvironmentPending(sessionId, id, null)
      if (version === generation.current) { setReceipt(result.receipt); setPending(null) }
    } catch (failure) {
      const rejected = failure instanceof ApiError && environmentRejectedBeforeWrite(failure.code)
      if (rejected) saveEnvironmentPending(sessionId, id, null)
      if (version === generation.current) {
        if (rejected) setPending(null)
        setError(failureText(failure))
      }
    } finally { writing.current = false; if (version === generation.current) setBusy(false) }
  }
  const reason = (value?: string) => t('ui.environment.reason.' + (['exists', 'not-ignored', 'symlink', 'unsafe', 'unsupported', 'missing'].includes(value ?? '') ? value : 'unsupported'))
  return <div className="min-w-0 space-y-3">
    <Button size="sm" icon={<FolderInput className="h-3.5 w-3.5" />} aria-expanded={open} disabled={busy || disabled} onClick={() => setOpen(!open)}>{t(open ? 'ui.environment.close' : 'ui.environment.open')}</Button>
    {open && <section aria-label={t('ui.environment.heading')} aria-busy={busy} className="min-w-0 space-y-3 border-t border-line pt-3">
      <p className="text-xs text-muted">{t('ui.environment.description')}</p>
      {error && <p role="alert" className="break-words text-xs text-danger">{noticeText(error)}</p>}
      {busy && <p role="status" className="text-xs text-muted">{t('ui.environment.busy')}</p>}
      {pending && !busy && <p role="status" className="text-xs text-warn">{t('ui.environment.unknown')}</p>}
      {receipt && <div className="space-y-1 rounded-lg bg-raised p-2" role="status">
        <p className="text-xs font-medium">{t('ui.environment.status.' + receipt.status)}</p>
        <p className="text-2xs text-muted">{receipt.status === 'preparing' ? t('ui.environment.planned', {count: receipt.planned_count}) : t('ui.environment.result', {count: receipt.copied_count, skipped: receipt.skipped_count})}</p>
        <p className="text-2xs text-faint">{t('ui.environment.history')}</p>
        {receipt.entries.length > 0 && <details><summary className="cursor-pointer text-2xs text-muted">{t('ui.environment.receipt')}</summary><ul className="mt-2 max-h-40 space-y-1 overflow-auto">{receipt.entries.map((entry, index) => <li key={index} className="text-2xs"><span className="break-all">{entry.path}</span><span className="ml-2 text-muted">{entry.action === 'copied' ? t('ui.environment.copied') : reason(entry.reason)}</span></li>)}</ul>{receipt.truncated && <p className="text-2xs text-faint">{t('ui.environment.truncated')}</p>}</details>}
      </div>}
      {preview && <>
        {!preview.configured ? <p className="break-words text-xs text-muted">{t('ui.environment.noManifest')}</p> : <p className="text-xs text-muted">{t('ui.environment.summary', {count: preview.copy_count, skipped: preview.skip_count, size: (preview.total_bytes / 1024).toFixed(1)})}</p>}
        {preview.entries.length > 0 && <ul className="max-h-52 space-y-1 overflow-auto rounded-lg border border-line p-2">{preview.entries.map(entry => <li key={entry.path} className="flex min-w-0 flex-wrap justify-between gap-x-3 gap-y-1 text-xs"><span className="min-w-0 break-all font-mono">{entry.path}</span><span className="text-muted">{entry.action === 'copy' ? t('ui.environment.toCopy', {size: ((entry.size ?? 0) / 1024).toFixed(1)}) : reason(entry.reason)}</span></li>)}</ul>}
        {preview.next_offset !== null && <ShowMoreControl disabled={busy || disabled} onClick={() => void load(preview.next_offset!)} />}
      </>}
      <div className="flex flex-wrap gap-2">
        {pending ? <Button size="sm" disabled={busy || disabled} onClick={() => void load()}>{t('ui.environment.check')}</Button>
          : <RefreshButton size="sm" label={t('ui.environment.refresh')} disabled={busy || disabled} onClick={() => void load()} />}
        {preview && preview.copy_count > 0 && <Button size="sm" variant="primary" disabled={busy || disabled || Boolean(pending)} onClick={() => void copy()}>{t('ui.environment.import', {count: preview.copy_count})}</Button>}
      </div>
      {pending && !busy && <details><summary className="cursor-pointer text-2xs text-faint">{t('ui.environment.recover')}</summary><p className="my-2 text-2xs text-muted">{t('ui.environment.recoverHint')}</p><Button size="xs" disabled={disabled} onClick={() => { mark(null); setPreview(null); void load() }}>{t('ui.environment.reviewed')}</Button></details>}
    </section>}
  </div>
}
