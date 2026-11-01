import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale, getLocale } from '@/i18n'
import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, ChevronRight, Clock3, Loader2, RotateCcw, TriangleAlert, XCircle } from 'lucide-react'
import type { RunRecord } from '@/lib/protocol.generated'
import { isRemoved, runDuration, runStatus, taskSummary } from '@/lib/runHistory'
import {failureText} from '@/lib/resourceError'
import {readRunTarget} from '@/lib/runReveal'
import {usePageVisible} from '@/hooks/usePageVisible'
import { cn, copyText, formatClock } from '@/lib/utils'
import { Button } from '@/components/ui/primitives'
import { Markdown } from '@/components/markdown/Markdown'
import { modelFailure } from '@/lib/modelFailure'
import { useAppStore } from '@/store/app'
import { JsonSource } from '@/components/ui/JsonSource'

function timestamp(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return t("ui.components.inspector.RunHistoryCard.7450cc5b16e2")
  const date = new Date(value * 1000)
  return Number.isFinite(date.getTime()) ? date.toLocaleString(getLocale(), { hour12: false }) : t("ui.components.inspector.RunHistoryCard.7450cc5b16e2")
}

export function RunHistoryCard({ record, sessionId, busy, context, onContinue, onOriginal, expanded = false, active: observing = true }: {
  record: RunRecord; sessionId: string; busy: boolean; expanded?: boolean; active?: boolean
  context?: { stopping: boolean; waiting: 'answer' | 'approval' | null }
  onContinue: (run: RunRecord) => void; onOriginal: (runId: string) => void
}) {
  useLocale()
  const visible = usePageVisible() && observing

  const [open, setOpen] = useState(expanded)
  const [detail, setDetail] = useState<RunRecord | null>(record.summary ? null : record)
  const [error, setError] = useState<ResourceText>('')
  const [refresh, setRefresh] = useState(0)
  const revision = useRef(0)
  useEffect(() => {
    if (!open || !visible) return
    const ticket = ++revision.current
    const controller = new AbortController()
    setError('')
    if (!record.summary && refresh === 0 && record.status !== 'running' && record.status !== 'stopping') {
      setDetail(record)
      return () => controller.abort()
    }
    setDetail(null)
    void readRunTarget({sessionId, runId: record.id, seq: 0}, controller.signal)
      .then((value) => { if (!controller.signal.aborted && ticket === revision.current) setDetail(value) })
      .catch(failure => { if (!controller.signal.aborted && ticket === revision.current) setError(failureText(failure)) })
    return () => controller.abort()
  }, [open, record.id, record.status, sessionId, refresh, visible])
  const active = record.status === 'running' || record.status === 'stopping'
  const removed = isRemoved(record)
  const failed = !removed && (record.status === 'failed' || record.status === 'interrupted')
  const Icon = active ? Loader2 : removed ? XCircle : record.status === 'completed' ? CheckCircle2 : failed ? TriangleAlert : Clock3
  const status = runStatus(record, context)
  const result = detail?.result
  const message = record.error?.message ?? record.error?.error ?? result?.error
  const copy = record.error?.copy ?? record.error?.details?.copy
  const modelError = modelFailure(message, record.error?.code)
  const duration = runDuration(record)
  return <article data-run-id={record.id} className="min-w-0 border-b border-line/75 py-3 last:border-b-0">
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="group flex cursor-pointer list-none items-start gap-2 rounded-md focus-visible:outline [&::-webkit-details-marker]:hidden">
        <Icon aria-hidden="true" className={cn('mt-0.5 h-4 w-4 shrink-0', active && 'animate-spin text-brand', failed ? 'text-warn' : 'text-muted')} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center justify-between gap-2 text-xs"><span className={cn('font-medium', failed && 'text-warn')}>{status}</span><span className="text-faint">{duration ?? t("ui.components.inspector.RunHistoryCard.7450cc5b16e2")}</span></span>
          <time className="mt-1 block text-2xs tabular-nums text-faint" title={timestamp(record.started_at ?? record.created_at)}>{formatClock(record.started_at ?? record.created_at)}</time>
          <span className="mt-1 break-words text-compact leading-5 text-ink line-clamp-2">{taskSummary(record)}</span>
        </span>
        <ChevronRight aria-hidden="true" className={cn('mt-0.5 h-3.5 w-3.5 shrink-0 text-faint transition-transform', open && 'rotate-90')} />
      </summary>
      <div className="mt-3 space-y-3 pl-6 text-xs" aria-label={t("ui.components.inspector.RunHistoryCard.fc224290be75")}>
        {error ? <div role="alert" className="space-y-2 text-danger"><p>{noticeText(error)}</p><Button size="sm" onClick={() => setRefresh((n) => n + 1)}>{t("ui.components.inspector.RunHistoryCard.7e8ad2dcd030")}</Button></div> : !detail ? <p className="flex items-center gap-2 text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" />{t("ui.components.inspector.RunHistoryCard.66e03739fedd")}</p> : <>
          <p className="whitespace-pre-wrap break-words leading-5">{detail.text}</p>
          <dl className="grid grid-cols-detail gap-x-3 gap-y-1.5 text-muted">
            {record.recovery ? <><dt>{t("ui.components.inspector.RunHistoryCard.08a4fbe4d6ae")}</dt><dd>{record.recovery.mode === 'inspect' ? t("ui.components.inspector.RunHistoryCard.25bbaae2598f") : t("ui.components.inspector.RunHistoryCard.151049e97283")}</dd>{record.recovery.verification_note ? <><dt>{t("ui.components.inspector.RunHistoryCard.478ed8e7f3c9")}</dt><dd className="whitespace-pre-wrap break-words">{record.recovery.verification_note}</dd></> : null}</> : null}
            <dt>{t("ui.components.inspector.RunHistoryCard.08a85f4ab4ba")}</dt><dd>{timestamp(record.created_at)}</dd>
            <dt>{t("ui.components.inspector.RunHistoryCard.d2bb025a2e51")}</dt><dd>{timestamp(record.started_at)}</dd>
            <dt>{t("ui.components.inspector.RunHistoryCard.c7b24e7997e9")}</dt><dd>{timestamp(record.finished_at)}</dd>
          </dl>
          {typeof result?.text === 'string' && result.text ? <div className="min-w-0 border-l-2 border-line pl-3 text-muted" aria-label={t("ui.components.inspector.RunHistoryCard.5ac291e54bf9")}><Markdown dense>{result.text}</Markdown></div> : null}
          {record.retry_of ? <Button size="sm" variant="ghost" disabled={busy} onClick={() => onOriginal(record.retry_of!)}>{t("ui.components.inspector.RunHistoryCard.272e7a151c04")}</Button> : null}
          <details className="text-muted"><summary className="cursor-pointer">{t("ui.components.inspector.RunHistoryCard.615d83ca0080")}</summary><div className="mt-2 space-y-2"><Button size="sm" variant="ghost" onClick={() => void copyText(record.id)}>{t("ui.components.inspector.RunHistoryCard.76fd07d924a4")}</Button><JsonSource text={JSON.stringify(detail, null, 2) ?? 'null'} className="max-h-64" active={visible} /></div></details>
        </>}
      </div>
    </details>
    {message || copy ? <div className="mt-2 space-y-1.5 pl-6 text-xs leading-5"><p className={cn('break-words', removed ? 'text-muted' : 'text-danger')}>{copy ? noticeText(copy) : modelError?.title ?? t('ui.runHistory.failureGeneric')}</p>{!copy && message ? <details className="text-muted"><summary className="cursor-pointer">{t("ui.components.inspector.RunHistoryCard.c26fb419db3c")}</summary>{record.error?.code ? <p className="mt-1">{String(record.error.code)}</p> : null}<p className="mt-1 whitespace-pre-wrap break-words">{String(message).slice(0, 4000)}</p><p className="mt-1">{t("ui.components.inspector.RunHistoryCard.92b8de53959a")}</p></details> : null}</div> : null}
    {failed ? <div className="mt-2 space-y-1.5 pl-6"><div className="flex flex-wrap gap-2"><Button size="sm" variant="ghost" disabled={busy} icon={<RotateCcw className="h-3.5 w-3.5" />} onClick={() => onContinue(record)}>{modelError ? t("ui.components.inspector.RunHistoryCard.f00c443e17a0") : t("ui.components.inspector.RunHistoryCard.b1cfb1916208")}</Button>{modelError ? <Button size="sm" variant="ghost" onClick={() => useAppStore.getState().setSettingsOpen(true, 'models')}>{t("ui.components.inspector.RunHistoryCard.64f06af28c10")}</Button> : null}</div><p className="text-xs leading-5 text-muted">{modelError?.hint ?? t("ui.components.inspector.RunHistoryCard.c034191dc646")}</p></div> : null}
  </article>
}
