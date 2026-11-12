import { t, useLocale } from '@/i18n'
import { useEffect, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'
import type { RunRecord } from '@/lib/protocol.generated'
import { readRunTarget, type RunRevealTarget } from '@/lib/runReveal'
import { ApiError } from '@/lib/http'
import { Button } from '@/components/ui/primitives'
import { RunHistoryCard } from './RunHistoryCard'
import {usePageVisible} from '@/hooks/usePageVisible'
import type {ResourceText} from '@/types'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'

type PreviewProps = {
  target: RunRevealTarget; busy: boolean; active?: boolean; onClose: () => void
  onContinue: (record: RunRecord) => void; onOriginal: (runId: string) => void
}

/** 目标身份独立拥有读取状态，关闭和替换不接收旧结果。 */
export function RunPreview(props: PreviewProps) {
  const {target} = props
  return <RunPreviewInstance key={JSON.stringify([target.sessionId, target.runId, target.turn, target.seq])} {...props} />
}

function RunPreviewInstance({ target, busy, active = true, onClose, onContinue, onOriginal }: PreviewProps) {
  useLocale()
  const visible = usePageVisible() && active

  const [record, setRecord] = useState<RunRecord | null>(null)
  const [error, setError] = useState<{message: ResourceText; unavailable: boolean} | null>(null)
  const [retry, setRetry] = useState(0)
  const heading = useRef<HTMLHeadingElement>(null)
  const focused = useRef(false)
  useEffect(() => {
    if (!visible || focused.current) return
    focused.current = true
    heading.current?.focus({ preventScroll: true })
    heading.current?.scrollIntoView({ block: 'nearest' })
  }, [visible])
  useEffect(() => {
    if (!visible) { setRecord(null); return }
    const controller = new AbortController()
    setRecord(null); setError(null)
    void readRunTarget(target, controller.signal)
      .then(value => { if (!controller.signal.aborted) setRecord(value) })
      .catch(failure => { if (!controller.signal.aborted) setError({message: failureText(failure),
        unavailable: failure instanceof ApiError && (failure.status === 404 || failure.code === 'run/ambiguous-turn')}) })
    return () => controller.abort()
  }, [target.sessionId, target.runId, target.turn, retry, visible])
  return <section className="rounded-lg border border-brand/25 bg-canvas px-3" aria-label={t("ui.components.inspector.RunPreview.874624ff7b10")} data-run-preview-turn={target.turn}>
    <div className="flex items-center justify-between gap-2 pt-3">
      <h3 ref={heading} tabIndex={-1} data-panel-initial-focus className="text-xs font-medium">{target.turn != null ? t("ui.components.inspector.RunPreview.ef79587fb63d", {p0: (target.turn)}) : t("ui.components.inspector.RunPreview.874624ff7b10")}</h3>
      <Button size="xs" variant="ghost" onClick={onClose}>{t("ui.components.inspector.RunPreview.e6a2caa875a5")}</Button>
    </div>
    {error ? <div role="alert" className="space-y-2 py-3 text-xs leading-reading">
      <p className="font-medium text-warn">{error.unavailable ? t("ui.components.inspector.RunPreview.1f6e8bb2b8b9") : t("ui.components.inspector.RunPreview.73773cc6f533")}</p>
      <p className="break-words text-muted">{noticeText(error.message)}</p>
      <Button size="xs" variant="secondary" disabled={!visible} onClick={() => setRetry(value => value + 1)}>{t("ui.components.inspector.RunPreview.ffbc7aa037bd")}</Button>
    </div> : record ? <RunHistoryCard key={record.id} record={record} sessionId={target.sessionId} busy={busy || !visible} active={visible} expanded
      onContinue={onContinue} onOriginal={onOriginal} />
      : <p role="status" aria-live="polite" className="flex items-center gap-2 py-3 text-xs text-muted">{visible ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> : null}{t(visible ? "ui.components.inspector.RunPreview.39b348fe6333" : 'ui.runPreview.readPaused')}</p>}
  </section>
}
