import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import { ShowMoreControl } from '@/components/ui/primitives'
import type { SkillAuditPage } from '@/lib/contextAudit'
import type { RequestTracePart } from '@/lib/requestTraces'
import { formatClock, formatNumber } from '@/lib/utils'

export function SkillAuditPanel({ sessionId, agentId, through }: { sessionId: string; agentId?: string; through: number }) {
  useLocale()

  const [opened, setOpened] = useState(false)
  const [data, setData] = useState<SkillAuditPage | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const controller = useRef<AbortController>()
  async function load(older = false) {
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    setLoading(true); setError('')
    const result = await api.skillAudit(sessionId, through, older ? data?.next_before ?? undefined : undefined, abort.signal, agentId)
    if (abort.signal.aborted) return
    setLoading(false)
    if (!result.ok) { setError(result.copy ?? result.message); return }
    setData(current => older && current ? { ...result.data, skills: [...result.data.skills, ...current.skills] } : result.data)
  }
  useEffect(() => { if (opened && !data) void load(); return () => controller.current?.abort() }, [opened, sessionId, agentId, through])
  return <details className="space-y-2 rounded-lg border border-line p-3" onToggle={e => setOpened(e.currentTarget.open)}>
    <summary className="cursor-pointer text-xs font-medium">{t("ui.components.inspector.SkillAuditPanel.a8adb2d2cf64")}</summary>
    <p className="text-2xs leading-relaxed text-faint">{t("ui.components.inspector.SkillAuditPanel.f318818b0cdc")}{through}{t("ui.components.inspector.SkillAuditPanel.66b943741593")}</p>
    {error ? <p role="alert" className="text-xs text-danger">{noticeText(error)}<button className="ml-2 text-brand" onClick={() => void load()}>{t("ui.components.inspector.SkillAuditPanel.b8784c8dd563")}</button></p> : null}
    {loading ? <p className="text-xs text-faint">{t("ui.components.inspector.SkillAuditPanel.f285c75fb1df")}</p> : null}
    {data?.has_more ? <ShowMoreControl disabled={loading} onClick={() => void load(true)} /> : null}
    {data && !data.skills.length ? <p className="text-xs text-faint">{t("ui.components.inspector.SkillAuditPanel.3010dac5ed77")}</p> : null}
    <ol className="space-y-2">{data?.skills.map(row => <li key={row.id} className="space-y-1 rounded border border-line p-2 text-2xs">
      <div className="flex justify-between gap-2"><span className="break-all font-medium">{row.name || t("ui.components.inspector.SkillAuditPanel.5c5fa9cc5521")}</span><time className="shrink-0 text-faint">{formatClock(row.time)}</time></div>
      <p className={row.status === 'failed' ? 'text-danger' : 'text-muted'}>{{ loaded: t("ui.components.inspector.SkillAuditPanel.dc466b648e05"), failed: t("ui.components.inspector.SkillAuditPanel.d1d044826a45"), unconfirmed: t("ui.components.inspector.SkillAuditPanel.a7cb0f1b3801") }[row.status]}{t("ui.components.inspector.SkillAuditPanel.416795d42575")}{row.id}</p>
      {row.status === 'loaded' ? <p className="text-faint">{{ full: t("ui.components.inspector.SkillAuditPanel.542011f30748"), pruned: t("ui.components.inspector.SkillAuditPanel.c8e090a7b487"), absent: t("ui.components.inspector.SkillAuditPanel.6dc4fd0ae720") }[row.context]}</p> : null}
      {row.error ? <p className="break-words text-danger">{row.error}</p> : null}
      {row.description ? <p className="break-words text-muted">{row.description}</p> : null}
      {row.result_seq !== null ? <button className="text-brand" onClick={() => setSelected(selected === row.result_seq ? null : row.result_seq)}>{t("ui.components.inspector.SkillAuditPanel.b3e0524dc88c")}</button> : null}
      {selected === row.result_seq && selected !== null ? <SkillReader key={selected} sessionId={sessionId} agentId={agentId} seq={selected} through={through} /> : null}
    </li>)}</ol>
  </details>
}

function SkillReader({ sessionId, agentId, seq, through }: { sessionId: string; agentId?: string; seq: number; through: number }) {
  useLocale()

  const [offset, setOffset] = useState(0)
  const [data, setData] = useState<RequestTracePart | null>(null)
  const [error, setError] = useState<ResourceText>('')
  useEffect(() => {
    const abort = new AbortController()
    setData(null); setError('')
    void api.skillOriginal(sessionId, seq, through, offset, abort.signal, agentId).then(result => {
      if (abort.signal.aborted) return
      if (result.ok) setData(result.data); else setError(result.copy ?? result.message)
    })
    return () => abort.abort()
  }, [sessionId, agentId, seq, through, offset])
  return <section aria-label={t("ui.components.inspector.SkillAuditPanel.440937167263")} className="space-y-2 pt-2">
    {error ? <p role="alert" className="text-danger">{noticeText(error)}</p> : data ? <><pre tabIndex={0} className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-raised p-2 font-mono text-xs">{data.text}</pre>
      <div className="flex justify-between gap-2 text-faint"><button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 24000))}>{t("ui.components.inspector.SkillAuditPanel.5c73eb92d825")}</button><span>{data.total_chars ? offset + 1 : 0}–{offset + Array.from(data.text).length} / {formatNumber(data.total_chars)}{t("ui.components.inspector.SkillAuditPanel.25dcebd656bb")}</span><button disabled={data.next_offset === null} onClick={() => setOffset(data.next_offset!)}>{t("ui.components.inspector.SkillAuditPanel.4cd4dc1f03d6")}</button></div></> : <p className="text-faint">{t("ui.components.inspector.SkillAuditPanel.346ff60e6c7c")}</p>}
  </section>
}
