import type {ResourceText} from '@/types'
import {failureText, ResourceError} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import { usePageVisible } from '@/hooks/usePageVisible'
import { t, useLocale } from '@/i18n'
import { useEffect, useRef, useState } from 'react'
import { runtimeRequest, ApiError } from '@/lib/http'
import type { PermissionGrant, PermissionGrants } from '@/lib/protocol.generated'
import { RefreshButton } from '@/components/ui/primitives'

export function TemporaryGrants({ sessionId, active = true }: { sessionId: string; active?: boolean }) {
  useLocale()
  const observing = usePageVisible() && active

  const [data, setData] = useState<PermissionGrants | null>(null)
  const [readError, setReadError] = useState<ResourceText>('')
  const [actionError, setActionError] = useState<ResourceText>('')
  const [decision, setDecision] = useState<ResourceText>('')
  const [warning, setWarning] = useState<ResourceText>('')
  const [busy, setBusy] = useState('')
  const [revision, setRevision] = useState(0)
  const action = useRef<AbortController | null>(null)
  const uncertain = useRef('')
  useEffect(() => () => { action.current?.abort() }, [])
  useEffect(() => {
    if (busy || !observing) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const read = async () => {
      try {
        const result = await runtimeRequest<PermissionGrants>(`/sessions/${encodeURIComponent(sessionId)}/permissions/grants`, 'GET', undefined, { signal: controller.signal })
        if (!controller.signal.aborted) {
          setData(result); setReadError('')
          if (uncertain.current && !result.grants.some((row) => row.id === uncertain.current)) {
            uncertain.current = ''; setActionError(''); setDecision({key: "ui.components.inspector.TemporaryGrants.dee5941bbb45"})
          }
        }
      } catch (reason) { if (!controller.signal.aborted) setReadError(failureText(reason)) }
      if (!controller.signal.aborted) timer = setTimeout(read, 2000)
    }
    void read()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [sessionId, revision, busy, observing])
  const revoke = async (id: string) => {
    const controller = new AbortController()
    action.current = controller
    setBusy(id); setWarning(''); setActionError(''); setDecision(''); uncertain.current = ''
    try {
      const result = await runtimeRequest<{ revoked: string; audit_error?: string }>(`/sessions/${encodeURIComponent(sessionId)}/permissions/grants/${encodeURIComponent(id)}`, 'DELETE', undefined, { signal: controller.signal })
      if (result.revoked !== id) throw new ResourceError({key: 'ui.permissions.grantReceiptMismatch'})
      if (!controller.signal.aborted) {
        setData((previous) => previous ? { ...previous, grants: previous.grants.filter((row) => row.id !== result.revoked) } : previous)
        setWarning(result.audit_error ? {key: 'ui.permissions.auditIncomplete'} : '')
        setDecision({key: "ui.components.inspector.TemporaryGrants.27f5da006428"})
      }
    } catch (reason) {
      if (!controller.signal.aborted) {
        if (reason instanceof ApiError && reason.code === 'permissions/grant-not-found') {
          setData((previous) => previous ? { ...previous, grants: previous.grants.filter((row) => row.id !== id) } : previous)
          setDecision({key: "ui.components.inspector.TemporaryGrants.dee5941bbb45"})
        } else {
          uncertain.current = id
          setActionError({template: {key: "ui.components.inspector.TemporaryGrants.9caf4baa9699", params: {detail: failureText(reason)}}})
        }
      }
    }
    finally { if (!controller.signal.aborted) { setBusy(''); setRevision((value) => value + 1) } }
  }
  return <section aria-label={t("ui.components.inspector.TemporaryGrants.21391506e2ee")} className="refresh-action-container mb-5 space-y-2 rounded-lg border border-line p-3">
    <div className="flex items-center justify-between gap-2"><h3 className="text-xs font-medium">{data ? t(data.grants.length === 1 ? 'ui.permissions.grantCountOne' : 'ui.permissions.grantCount', {count: data.grants.length}) : t('ui.components.inspector.TemporaryGrants.21391506e2ee')}</h3>
      <RefreshButton size="xs" variant="ghost" label={t("ui.components.inspector.TemporaryGrants.f23bd9d6b9d5")} disabled={Boolean(busy)} onClick={() => setRevision((value) => value + 1)} /></div>
    <p className="text-2xs leading-relaxed text-muted">{t("ui.components.inspector.TemporaryGrants.1ff04d1c50ad")}</p>
    {data?.parent_controlled ? <p className="text-2xs text-muted">{t("ui.components.inspector.TemporaryGrants.0883d6e21059")}</p> : null}
    {readError ? <p role="alert" className="text-2xs text-danger">{t(data ? "ui.components.inspector.TemporaryGrants.e2e8ade5bad2" : "ui.components.inspector.TemporaryGrants.d25e77624abb", {detail: noticeText(readError)})}</p> : null}
    {actionError ? <p role="alert" className="text-2xs text-danger">{noticeText(actionError)}</p> : null}
    {decision ? <p role="status" className="text-2xs text-muted">{noticeText(decision)}</p> : null}
    {warning ? <p role="alert" className="text-2xs text-warn">{noticeText(warning)}</p> : null}
    {!data && !readError ? <p role="status" className="text-2xs text-muted">{t("ui.components.inspector.TemporaryGrants.869eee83ffb5")}</p> : null}
    {data && !data.grants.length ? <p className="text-2xs text-faint">{t("ui.components.inspector.TemporaryGrants.2827e8ce27cd")}</p> : null}
    {data?.grants.map((row) => <GrantRow key={row.id} sessionId={sessionId} row={row} disabled={Boolean(busy) || data.parent_controlled} onRevoke={() => void revoke(row.id)} />)}
  </section>
}

function GrantRow({ sessionId, row, disabled, onRevoke }: { sessionId: string; row: PermissionGrant; disabled: boolean; onRevoke: () => void }) {
  useLocale()

  const [open, setOpen] = useState(false)
  const [operation, setOperation] = useState('')
  const [error, setError] = useState<ResourceText>('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setError('')
    void runtimeRequest<{ grant: PermissionGrant }>(`/sessions/${encodeURIComponent(sessionId)}/permissions/grants/${encodeURIComponent(row.id)}`, 'GET', undefined, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setOperation(data.grant.operation ?? '') })
      .catch((reason) => { if (!controller.signal.aborted) setError(failureText(reason)) })
    return () => controller.abort()
  }, [sessionId, row.id, open, retry])
  return <div className="space-y-1 border-t border-line pt-2 text-2xs">
    <p className="break-all font-medium">{t('ui.permissions.grantRemaining', {tool: row.tool, seconds: row.remaining_seconds})}</p>
    <p className="break-all text-muted">{t("ui.components.inspector.TemporaryGrants.d2c35d3b2c71")}{row.cwd}</p>
    <details onToggle={(event) => { setOpen(event.currentTarget.open); if (!event.currentTarget.open) setOperation('') }}>
      <summary className="cursor-pointer text-brand">{t("ui.components.inspector.TemporaryGrants.691851ee4c14")}</summary>
      {error ? <p role="alert" className="text-danger">{noticeText(error)}<button type="button" className="ml-2 underline" onClick={() => setRetry((value) => value + 1)}>{t("ui.components.inspector.TemporaryGrants.211e1f4d639f")}</button></p> : <pre tabIndex={0} aria-label={t("ui.components.inspector.TemporaryGrants.ebb69abe0888")} className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all text-muted">{operation || t("ui.components.inspector.TemporaryGrants.86b6d0d63062")}</pre>}
    </details>
    <button type="button" disabled={disabled} onClick={onRevoke} className="rounded px-2 py-1 text-danger hover:bg-raised disabled:opacity-40">{t("ui.components.inspector.TemporaryGrants.bf368f79fae5")}</button>
  </div>
}
