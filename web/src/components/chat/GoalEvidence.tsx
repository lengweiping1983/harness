import type { ResourceText } from '@/types'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { useEffect, useId, useState } from 'react'
import { CheckCircle2, Circle, X } from 'lucide-react'
import type { GoalEvidencePage, GoalEvidenceRef, GoalState } from '@/types'
import { api, unwrap } from '@/lib/api'
import { Button, Overlay } from '@/components/ui/primitives'

type Goal = NonNullable<GoalState['goal']>
export function GoalEvidence({ sessionId, goal }: { sessionId: string; goal: Goal }) {
  useLocale()

  const [selected, setSelected] = useState<GoalEvidenceRef | null>(null)
  const checks = goal.completion ?? []
  return <div className="space-y-2" aria-label={t("ui.components.chat.GoalEvidence.ce2576aa69e0")}>
    <p className="text-xs font-medium text-ink">{t("ui.components.chat.GoalEvidence.68ca29ff2300")}{checks.length > 0 ? t("ui.components.chat.GoalEvidence.1b27a5416c9b", {p0: (checks.length), p1: (goal.criteria?.length ?? 1)}) : ''}</p>
    <ol className="space-y-2">
      {(goal.criteria ?? [goal.objective]).map((criterion, index) => {
        const check = checks.find(row => row.criterion === index + 1)
        const Icon = check ? CheckCircle2 : Circle
        return <li key={index} className="flex items-start gap-2 text-xs leading-5">
          <Icon className={'mt-0.5 h-3.5 w-3.5 shrink-0 ' + (check ? 'text-ok' : 'text-faint')} aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <p className="whitespace-pre-wrap break-words text-ink">{criterion}</p>
            {check && <>
              <p className="whitespace-pre-wrap break-words text-muted">{check.summary}</p>
              <div className="flex flex-wrap gap-2 pt-1">
                {check.evidence.map(ref => <button key={ref.sourceSeq} type="button" className="rounded border border-line bg-base px-2 text-brand hover:bg-raised"
                  title={ref.input} onClick={() => setSelected(ref)}>{t("ui.components.chat.GoalEvidence.a8a74a6f379f")}{ref.name} · #{ref.sourceSeq}</button>)}
              </div>
            </>}
          </div>
        </li>
      })}
    </ol>
    {goal.phase === 'complete' && !checks.length && <p className="text-xs text-muted">{t("ui.components.chat.GoalEvidence.37e5e7af5793")}</p>}
    {!!checks.length && <p className="text-xs leading-5 text-faint">{t("ui.components.chat.GoalEvidence.8ffff5d1098c")}</p>}
    {selected && <EvidenceDialog key={`${sessionId}:${goal.id}:${selected.sourceSeq}`} sessionId={sessionId} goalId={goal.id} reference={selected} onClose={() => setSelected(null)} />}
  </div>
}

function EvidenceDialog({ sessionId, goalId, reference, onClose }: { sessionId: string; goalId: string; reference: GoalEvidenceRef; onClose: () => void }) {
  useLocale()

  const heading = useId()
  const [offset, setOffset] = useState(0)
  const [retry, setRetry] = useState(0)
  const [page, setPage] = useState<GoalEvidencePage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ResourceText>('')
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(''); setPage(null)
    void unwrap(api.goalEvidence(sessionId, goalId, reference.sourceSeq, offset, controller.signal)).then(data => {
      if (!controller.signal.aborted) setPage(data.evidence)
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? failureText(reason) : {key: "ui.components.chat.GoalEvidence.6aa27f14bff1"}) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [sessionId, goalId, reference.sourceSeq, offset, retry])
  return <Overlay onClose={onClose} labelledBy={heading} className="mt-dialog-compact max-w-3xl">
    <div className="space-y-3 p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 id={heading} className="text-sm font-medium text-ink">{t("ui.components.chat.GoalEvidence.1cd47ce10e05")}{reference.name} · #{reference.sourceSeq}</h2>
        <Button aria-label={t("ui.components.chat.GoalEvidence.398649af8d30")} variant="ghost" onClick={onClose}><X className="h-4 w-4" /></Button>
      </div>
      <p className="break-all text-xs text-muted">{t("ui.components.chat.GoalEvidence.9d62cc3097bf")}{reference.input}</p>
      <p className="text-xs text-muted">{t("ui.components.chat.GoalEvidence.283884c474a6")}</p>
      {loading && <p role="status" className="text-xs text-muted">{t("ui.components.chat.GoalEvidence.c5ceaa7b0f43")}</p>}
      {error && <p role="alert" className="text-xs text-danger">{noticeText(error)}<button className="ml-2 underline" onClick={() => setRetry(value => value + 1)}>{t("ui.components.chat.GoalEvidence.1133d6759e10")}</button></p>}
      {page && <>
        <p className="text-xs tabular-nums text-faint">{t("ui.components.chat.GoalEvidence.a7554f37e67b")}{page.offset}–{page.nextOffset ?? page.totalChars} / {page.totalChars}</p>
        <pre tabIndex={0} data-offset={page.offset} aria-label={t("ui.components.chat.GoalEvidence.ea00ea8688c8")} className="max-h-evidence overflow-auto whitespace-pre-wrap break-all rounded-lg border border-line bg-base p-3 text-xs text-ink">{page.text}</pre>
        <div className="flex gap-2">
          <Button disabled={loading || page.offset === 0} onClick={() => setOffset(Math.max(0, page.offset - 20000))}>{t("ui.components.chat.GoalEvidence.4ec383b05449")}</Button>
          <Button disabled={loading || page.nextOffset === null} onClick={() => setOffset(page.nextOffset ?? 0)}>{t("ui.components.chat.GoalEvidence.88bd2000002d")}</Button>
        </div>
      </>}
    </div>
  </Overlay>
}
