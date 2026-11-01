import {ResourceError, failureText} from '@/lib/resourceError'
import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { runtimeRequest } from '@/lib/http'
import type { PlanEvidencePage, TodoEvidence } from '@/lib/protocol.generated'
import { evidenceReaderKey, readEvidenceReader, saveEvidenceReader } from '@/lib/evidenceReader'

type Props = { sessionId: string; planId: string; evidence: TodoEvidence; active?: boolean }

/** Load one bounded page at a time; closing or changing ownership cancels requests. */
export function PlanEvidence(props: Props) {
  useLocale()

  const key = evidenceReaderKey(props.sessionId, props.planId, props.evidence.source_seq, props.evidence.call_id)
  return <EvidenceReader key={key} readerKey={key} {...props} />
}

function EvidenceReader({ sessionId, planId, evidence, readerKey, active = true }: Props & { readerKey: string }) {
  useLocale()

  const [restored] = useState(() => readEvidenceReader(readerKey))
  const [open, setOpen] = useState(restored.open)
  const [offset, setOffset] = useState<number | null>(restored.offset)
  const scrollTop = useRef(restored.scrollTop)
  const original = useRef<HTMLPreElement>(null)
  const [page, setPage] = useState<PlanEvidencePage | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const [loading, setLoading] = useState(false)
  const [retry, setRetry] = useState(0)
  useEffect(() => { saveEvidenceReader(readerKey, { open, offset, scrollTop: scrollTop.current }) }, [readerKey, open, offset])
  useLayoutEffect(() => { if (page && original.current) original.current.scrollTop = scrollTop.current }, [page])
  const goTo = (next: number | null) => { scrollTop.current = 0; setOffset(next) }
  useEffect(() => {
    if (!active || offset === null) return
    const controller = new AbortController()
    setLoading(true); setError(''); setPage(null)
    const path = `/sessions/${encodeURIComponent(sessionId)}/plans/${encodeURIComponent(planId)}/evidence/${evidence.source_seq}?offset=${offset}`
    void runtimeRequest<{ evidence: PlanEvidencePage }>(path, 'GET', undefined, { signal: controller.signal }).then((data) => {
      if (controller.signal.aborted) return
      const value = data?.evidence
      const end = value && typeof value.text === 'string' ? value.offset + Array.from(value.text).length : -1
      if (!value || value.call_id !== evidence.call_id || value.source_seq !== evidence.source_seq || value.name !== evidence.name
          || value.offset !== offset || !Number.isSafeInteger(value.total_chars) || value.total_chars < 0
          || end < offset || end > value.total_chars || value.next_offset !== (end < value.total_chars ? end : null)
          || end === offset && end < value.total_chars) throw new ResourceError({key: 'ui.http.invalidResponse'})
      setPage(value)
    }).catch((reason) => { if (!controller.signal.aborted) setError(failureText(reason)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [sessionId, planId, evidence.source_seq, offset, retry, active])
  return <details open={open} className="rounded bg-raised px-2 py-1" onToggle={(event) => {
    setOpen(event.currentTarget.open)
    if (!event.currentTarget.open) { goTo(null); setPage(null); setError(''); setLoading(false) }
  }}>
    <summary className="cursor-pointer break-all text-2xs text-brand">{t("ui.components.inspector.PlanEvidence.aaefc1df18ff")}{evidence.name} · #{evidence.source_seq}</summary>
    <pre tabIndex={0} aria-label={t("ui.components.inspector.PlanEvidence.daa0c113b4f0", {p0: (evidence.call_id)})} className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all text-2xs text-muted">{evidence.summary}</pre>
    {evidence.truncated ? <p className="text-2xs text-faint">{t("ui.components.inspector.PlanEvidence.dbe5105db742")}</p> : null}
    <p className="mt-1 break-all text-2xs text-faint">{t("ui.components.inspector.PlanEvidence.1c846732e232")}{evidence.call_id}</p>
    {offset === null ? <button type="button" onClick={() => goTo(0)} className="mt-1 rounded px-2 py-1 text-2xs text-brand hover:bg-surface">{t("ui.components.inspector.PlanEvidence.b04c041e6e59")}</button> : <div className="mt-2 space-y-2 border-t border-line pt-2">
      <p className="text-2xs text-muted">{t("ui.components.inspector.PlanEvidence.283884c474a6")}</p>
      {loading ? <p role="status" className="text-2xs text-muted">{t("ui.components.inspector.PlanEvidence.bb63dbce0f74")}</p> : null}
      {error ? <div role="alert" className="text-2xs text-danger">{noticeText(error)}<button type="button" onClick={() => setRetry((value) => value + 1)} className="ml-2 underline">{t("ui.components.inspector.PlanEvidence.1133d6759e10")}</button></div> : null}
      {page ? <>
        <p className="text-2xs text-faint">{t("ui.components.inspector.PlanEvidence.a7554f37e67b")}{page.offset}–{page.next_offset ?? page.total_chars} / {page.total_chars}</p>
        <pre ref={original} tabIndex={0} aria-label={t("ui.components.inspector.PlanEvidence.c3da6b4a8743", {p0: (evidence.call_id)})} onScroll={(event) => {
          scrollTop.current = Math.round(event.currentTarget.scrollTop)
          saveEvidenceReader(readerKey, { open, offset, scrollTop: scrollTop.current })
        }} className="max-h-72 overflow-auto whitespace-pre-wrap break-all text-2xs text-ink">{page.text}</pre>
        <div className="flex flex-wrap gap-2 text-2xs">
          <button type="button" disabled={loading || page.offset === 0} onClick={() => goTo(Math.max(0, page.offset - 20000))} className="rounded px-2 py-1 text-brand hover:bg-surface disabled:opacity-40">{t("ui.components.inspector.PlanEvidence.6ac8d6ee898d")}</button>
          <button type="button" disabled={loading || page.next_offset === null} onClick={() => goTo(page.next_offset)} className="rounded px-2 py-1 text-brand hover:bg-surface disabled:opacity-40">{t("ui.components.inspector.PlanEvidence.ea904f36c20b")}</button>
        </div>
      </> : null}
    </div>}
  </details>
}
