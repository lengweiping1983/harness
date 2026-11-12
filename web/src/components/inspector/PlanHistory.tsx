import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ClipboardCheck } from 'lucide-react'
import { RefreshButton } from '@/components/ui/primitives'
import { useChat } from '@/store/chat'
import { runtimeRequest, ApiError } from '@/lib/http'
import { Markdown } from '@/components/markdown/Markdown'
import { DiffView } from '@/components/chat/blocks/DiffView'
import { PlanEvidence } from './PlanEvidence'
import { cn } from '@/lib/utils'
import type { PlanDetail, PlanVersion } from '@/lib/protocol.generated'
import { readPlanReader, savePlanReader, selectedPlan, readPlanScroll, savePlanScroll } from '@/lib/planReader'

const labels: Record<string, string> = { get pending() { return t("ui.components.inspector.PlanHistory.406be06ced7c") }, get revision_requested() { return t("ui.components.inspector.PlanHistory.ecaa3aaacadf") }, get approved() { return t("ui.components.inspector.PlanHistory.b42cb548c1d4") }, get closed() { return t("ui.components.inspector.PlanHistory.c653b5c2a5a3") }, get superseded() { return t("ui.components.inspector.PlanHistory.84fb40a63de4") } }

/** 运行修订合并到读取结束后，避免连续事件取消尚未返回的快照。 */
function useReadRevision(revision: string, loading: boolean) {
  const [settled, setSettled] = useState(revision)
  useEffect(() => { if (!loading) setSettled(revision) }, [revision, loading])
  return settled
}

function PlanBody({ sessionId, plan }: { sessionId: string; plan: PlanDetail }) {
  useLocale()

  const body = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    if (body.current) body.current.scrollTop = readPlanScroll(sessionId, plan.id)
  }, [sessionId, plan.id])
  return <div ref={body} aria-label={t("ui.components.inspector.PlanHistory.35401e0dc332")} tabIndex={0}
    onScroll={(event) => savePlanScroll(sessionId, plan.id, event.currentTarget.scrollTop)}
    className="max-h-history min-w-0 overflow-auto rounded-lg border border-line bg-surface p-3 [overflow-wrap:anywhere]">
    <Markdown dense>{plan.plan ?? ''}</Markdown>
  </div>
}

export function PlanHistory({ sessionId, active = true }: { sessionId: string | null; active?: boolean }) {
  useLocale()

  const chat = useChat(sessionId)
  const [restored] = useState(() => readPlanReader(sessionId))
  const [plans, setPlans] = useState<PlanVersion[]>([])
  const [selected, setSelected] = useState(restored.selected)
  const [loadedDetail, setDetail] = useState<PlanDetail | null>(null)
  const [listError, setListError] = useState<ResourceText>('')
  const [detailError, setDetailError] = useState<ResourceText>('')
  const [loading, setLoading] = useState(false)
  const [listLoading, setListLoading] = useState(Boolean(sessionId))
  const [compare, setCompare] = useState(restored.compare)
  const [followLatest, setFollowLatest] = useState(restored.followLatest)
  const following = useRef(restored.followLatest)
  const [listRetry, setListRetry] = useState(0)
  const [detailRetry, setDetailRetry] = useState(0)
  const listRevision = useReadRevision(JSON.stringify([chat.planRevision, chat.connection, chat.running]), listLoading)
  const detailRevision = useReadRevision(JSON.stringify([chat.planRevision, chat.connection]), loading)
  const refresh = () => { setListRetry((value) => value + 1); setDetailRetry((value) => value + 1) }
  const previousSelected = useRef(selected)
  useEffect(() => {
    if (previousSelected.current !== selected) setCompare(false)
    previousSelected.current = selected
  }, [selected])
  useEffect(() => { savePlanReader(sessionId, { selected, followLatest, compare }) }, [sessionId, selected, followLatest, compare])
  useEffect(() => {
    if (!active || !sessionId) return
    const controller = new AbortController()
    setListLoading(true)
    void runtimeRequest<{ plans: PlanVersion[] }>(`/sessions/${encodeURIComponent(sessionId)}/plans`, 'GET', undefined, { signal: controller.signal }).then((data) => {
      if (controller.signal.aborted) return
      setListError('')
      setPlans(data.plans)
      setSelected((id) => selectedPlan(id, following.current, data.plans.map((plan) => plan.id)))
    }).catch((error) => {
      if (controller.signal.aborted) return
      setListError(error instanceof ApiError && error.failure.copy ? error.failure.copy : (error as Error).message)
      if (error instanceof ApiError && [401, 403, 404, 410].includes(error.status)) { setPlans([]); setSelected('') }
    }).finally(() => { if (!controller.signal.aborted) setListLoading(false) })
    return () => controller.abort()
  }, [sessionId, listRevision, listRetry, active])
  const validSelected = plans.some((plan) => plan.id === selected)
  const detail = validSelected && loadedDetail?.id === selected ? loadedDetail : null
  useEffect(() => {
    if (!active) return
    if (!sessionId || !selected || !validSelected) { setDetail(null); setDetailError(''); setLoading(false); return }
    const controller = new AbortController()
    setLoading(true); setDetail((previous) => previous?.id === selected ? previous : null); setDetailError('')
    void runtimeRequest<{ plan: PlanDetail }>(`/sessions/${encodeURIComponent(sessionId)}/plans/${encodeURIComponent(selected)}`, 'GET', undefined, { signal: controller.signal }).then((data) => {
      if (!controller.signal.aborted) setDetail(data.plan)
    }).catch((error) => {
      if (controller.signal.aborted) return
      setDetailError(error instanceof ApiError && error.failure.copy ? error.failure.copy : (error as Error).message)
      if (error instanceof ApiError && [401, 403, 404, 410].includes(error.status)) setDetail(null)
    })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [sessionId, selected, validSelected, detailRevision, detailRetry, active])
  return <section aria-label={t("ui.components.inspector.PlanHistory.7c955177f6b1")} className="refresh-action-container space-y-3">
    <div className="flex items-center justify-between gap-2">
      <h3 className="flex items-center gap-1.5 text-sm font-medium"><ClipboardCheck className="h-4 w-4 shrink-0 text-brand" />{plans.length ? t(plans.length === 1 ? 'ui.planHistory.titleOne' : 'ui.planHistory.titleCount', {count: plans.length}) : t("ui.components.inspector.PlanHistory.17e858f193da")}</h3>
      <RefreshButton size="xs" variant="ghost" label={t("ui.components.inspector.PlanHistory.f7c3515f4e39")} onClick={refresh} spinning={loading || listLoading} />
    </div>
    {listLoading ? <p role="status" className="text-xs text-muted">{t("ui.components.inspector.PlanHistory.a0b7870b2c73")}</p> : null}
    {listError ? <div role="alert" aria-label={t("ui.components.inspector.PlanHistory.d3376fb397b7")} className="space-y-1 text-xs text-danger">
      <p>{t("ui.components.inspector.PlanHistory.f638729cdd26")}{noticeText(listError)}</p>
      {plans.length ? <p className="text-muted">{t("ui.components.inspector.PlanHistory.87a09c00355b")}</p> : null}
      <button type="button" onClick={() => setListRetry((value) => value + 1)} className="underline">{t("ui.components.inspector.PlanHistory.5c2768541a36")}</button>
    </div> : null}
    {detailError ? <div role="alert" aria-label={t("ui.components.inspector.PlanHistory.f22263c66827")} className="space-y-1 text-xs text-danger">
      <p>{t("ui.components.inspector.PlanHistory.d58168435968")}{noticeText(detailError)}</p>
      <button type="button" onClick={() => setDetailRetry((value) => value + 1)} className="underline">{t("ui.components.inspector.PlanHistory.af6c8efaf708")}</button>
    </div> : null}
    {!plans.length && !listLoading && !listError ? <p className="text-xs leading-relaxed text-muted">{t("ui.components.inspector.PlanHistory.3b64f9f57486")}</p> : null}
    {plans.length ? <>
      <div className="text-xs text-muted">
        <select aria-label={t("ui.components.inspector.PlanHistory.ec73e2bcf32b")} value={selected} onChange={(event) => { following.current = false; setFollowLatest(false); setSelected(event.target.value) }} className="w-full min-w-0 rounded-lg border border-line bg-surface px-2 py-2 text-ink">
          {[...plans].reverse().map((plan) => <option key={plan.id} value={plan.id}>v{plans.indexOf(plan) + 1} · {plan.title} · {labels[plan.status] ?? plan.status}</option>)}
        </select>
      </div>
      <div className="space-y-1 text-2xs">
        <button type="button" aria-pressed={followLatest} onClick={() => {
          following.current = !followLatest; setFollowLatest(!followLatest)
          if (!followLatest) setSelected(plans.at(-1)?.id ?? '')
        }} className="rounded px-2 py-1 text-brand hover:bg-raised">{t("ui.components.inspector.PlanHistory.8cc4d4922bf8")}</button>
        <p className="text-muted">{followLatest ? t("ui.components.inspector.PlanHistory.5bc7b0101414") : t("ui.components.inspector.PlanHistory.9be3848b8372")}</p>
        {!followLatest && selected !== plans.at(-1)?.id ? <button type="button" onClick={() => {
          following.current = true; setFollowLatest(true); setSelected(plans.at(-1)?.id ?? '')
        }} className="rounded px-2 py-1 text-brand hover:bg-raised">{t("ui.components.inspector.PlanHistory.90f85f2f36d6")}</button> : null}
      </div>
      {loading ? <p role="status" className="text-xs text-muted">{t("ui.components.inspector.PlanHistory.d2c7b91edaee")}</p> : null}
      {detail ? <>
        {loading || detailError ? <p role="status" className="rounded-lg border border-line bg-raised p-2 text-xs leading-relaxed text-muted">{t("ui.components.inspector.PlanHistory.4626481a4589")}</p> : null}
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className={cn('rounded-md px-2 py-1', detail.status === 'approved' ? 'bg-ok/10 text-ok' : 'bg-raised text-muted')}>{labels[detail.status] ?? detail.status}</span>
          {detail.previous_id ? <button type="button" aria-pressed={compare} onClick={() => setCompare(!compare)} className="rounded px-2 py-1 text-brand hover:bg-raised">{compare ? t("ui.components.inspector.PlanHistory.d1c8e47d37c9") : t("ui.components.inspector.PlanHistory.27d43b5ac024")}</button> : null}
        </div>
        {detail.feedback ? <p className="whitespace-pre-wrap break-words rounded-lg border-l-2 border-brand bg-raised p-2 text-xs leading-relaxed"><span className="block font-medium">{t("ui.components.inspector.PlanHistory.bcdf6914e2da")}</span>{detail.feedback}</p> : null}
        {compare ? <div aria-label={t("ui.components.inspector.PlanHistory.3b5d545a557c")}>{detail.diff ? <DiffView output={detail.diff} display={{ diff: detail.diff }} path="plan.md" /> : <p className="text-xs text-muted">{t("ui.components.inspector.PlanHistory.9a1ef3246b35")}</p>}</div> : sessionId ? <PlanBody key={`${sessionId}:${detail.id}`} sessionId={sessionId} plan={detail} /> : null}
        {detail.status === 'approved' ? <p className="text-2xs leading-relaxed text-muted">{t("ui.components.inspector.PlanHistory.e049277089cd")}</p> : null}
        {detail.status === 'approved' ? <section aria-label={t("ui.components.inspector.PlanHistory.c3ee05b59c24")} className="space-y-2 rounded-lg border border-line p-3">
          <h4 className="text-xs font-medium">{t("ui.components.inspector.PlanHistory.0a17a94275ef")}{(detail.implementation ?? []).filter((item) => item.status === 'completed').length}/{detail.implementation?.length ?? 0}</h4>
          <p className="text-2xs leading-relaxed text-muted">{t("ui.components.inspector.PlanHistory.2180c3b22854")}</p>
          {!detail.implementation?.length ? <p className="text-xs text-faint">{t("ui.components.inspector.PlanHistory.4876818b708d")}</p> : <ol className="space-y-2">
            {detail.implementation.map((item) => <li key={item.id ?? item.content} className="space-y-1 border-t border-line/60 pt-2 text-xs">
              <p className="break-words"><span className="mr-1 text-faint">{item.status === 'completed' ? t("ui.components.inspector.PlanHistory.6d4ca48a1eae") : item.status === 'in_progress' ? t("ui.components.inspector.PlanHistory.dc9591e56d50") : t("ui.components.inspector.PlanHistory.5b715411b1f9")} ·</span>{item.content}</p>
              {!item.evidence?.length ? <p className="text-2xs text-faint">{t("ui.components.inspector.PlanHistory.4f2ff85dffd8")}</p> : sessionId ? item.evidence.map((evidence) => <PlanEvidence key={`${detail.id}:${evidence.source_seq}`} sessionId={sessionId} planId={detail.id} evidence={evidence} active={active} />) : null}
            </li>)}
          </ol>}
        </section> : null}
      </> : null}
    </> : null}
  </section>
}
