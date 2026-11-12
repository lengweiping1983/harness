import type {HookActivity, ResourceText} from '@/types'
import {ResourceError, failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { useEffect, useState } from 'react'
import { Check, ChevronRight, GitBranch, Loader2, TriangleAlert } from 'lucide-react'
import type { HookEvidence } from '@/lib/protocol.generated'
import { hookEvidencePath, hookImpact, hookPhases, hookStateLabel } from '@/lib/hookActivity'
import { runtimeRequest } from '@/lib/http'
import { cn, formatDuration } from '@/lib/utils'
import { CopyButton } from '@/components/ui/primitives'
import { useAppStore } from '@/store/app'

export function HookActivityList({ rows, sessionId }: { rows: HookActivity[]; sessionId: string | null }) {
  useLocale()

  const [showPassed, setShowPassed] = useState(false)
  const first = rows[0]
  if (!first) return null
  const attention = rows.some((row) => row.status !== 'passed')
  const visible = attention || showPassed
  return <section aria-label={t("ui.components.chat.blocks.HookActivity.7b3604734d4e")} className="ml-5 mt-1 min-w-0 border-l border-line pl-4">
    {attention ? <div className="flex min-h-7 items-center gap-1.5 px-1 text-2xs text-muted"><GitBranch aria-hidden className="h-3 w-3" /><span>{t('ui.hooks.groupChecks', {phase: first.event === 'Stop' ? t('ui.components.chat.blocks.HookActivity.b8b25c6d8362') : t('ui.components.chat.blocks.HookActivity.926b04e8ced4'), count: rows.length})}</span></div> : <button type="button" aria-expanded={visible} aria-controls={'hook-list-' + first.id}
      onClick={() => setShowPassed((value) => !value)}
      className="flex min-h-7 max-w-full items-center gap-1.5 rounded px-1 text-2xs text-muted hover:bg-raised">
      <GitBranch aria-hidden className="h-3 w-3 shrink-0" /><span>{t('ui.hooks.groupPassed', {phase: first.event === 'Stop' ? t('ui.components.chat.blocks.HookActivity.b8b25c6d8362') : t('ui.components.chat.blocks.HookActivity.926b04e8ced4'), count: rows.length})}</span>
      <ChevronRight aria-hidden className={cn('h-3 w-3', visible && 'rotate-90')} />
    </button>}
    {visible ? <div id={'hook-list-' + first.id} className="space-y-1 pb-1">{rows.map((row) => <HookRow key={row.id} row={row} sessionId={sessionId} onExpand={() => setShowPassed(true)} />)}</div> : null}
  </section>
}

/** The statistics dialog exposes individual records directly, without another group toggle. */
export function HookActivityRecords({ rows, sessionId }: { rows: HookActivity[]; sessionId: string | null }) {
  useLocale()

  return <div className="space-y-1">{rows.map(row => <HookRow key={row.id} row={row} sessionId={sessionId} record onExpand={() => {}} />)}</div>
}

function HookRow({ row, sessionId, onExpand, record = false }: { row: HookActivity; sessionId: string | null; onExpand: () => void; record?: boolean }) {
  useLocale()

  const [open, setOpen] = useState(false)
  const [retry, setRetry] = useState(0)
  const [response, setResponse] = useState<{ key: string; value?: HookEvidence; error?: ResourceText } | null>(null)
  const key = JSON.stringify([sessionId, row.id, row.source_seq, row.status])
  const current = response?.key === key ? response : null
  const running = row.status === 'running'
  const issue = !['running', 'passed', 'skipped'].includes(row.status)
  const Icon = running ? Loader2 : row.status === 'passed' ? Check : TriangleAlert
  const impact = hookImpact(row)
  useEffect(() => {
    if (!open || !sessionId || row.session_id !== sessionId) return
    const controller = new AbortController()
    setResponse(null)
    void runtimeRequest<{ hook: HookEvidence }>(hookEvidencePath(sessionId, row), 'GET', undefined, { signal: controller.signal }).then(({ hook }) => {
      if (controller.signal.aborted) return
      if (hook.id !== row.id || hook.session_id !== sessionId || hook.call_id !== row.call_id || hook.source_seq !== row.source_seq || hook.root !== row.root) throw new ResourceError({key: "ui.components.chat.blocks.HookActivity.04285ea11186"})
      setResponse({ key, value: hook })
    }).catch((error: Error) => { if (!controller.signal.aborted) setResponse({ key, error: failureText(error) }) })
    return () => controller.abort()
  }, [open, sessionId, key, retry])
  const detail = current?.value
  return <article data-hook-run-id={row.id} className="min-w-0 rounded-md">
    <button type="button" onClick={() => { if (!open) onExpand(); setOpen((value) => !value) }} aria-expanded={open} aria-controls={'hook-detail-' + row.id}
      className={cn('flex w-full min-w-0 items-center gap-2 rounded-md text-left hover:bg-raised', record ? 'min-h-11 flex-wrap px-1 py-2 text-sm' : 'min-h-8 px-1 py-1 text-xs', issue ? 'text-danger' : 'text-muted')}>
      {record ? <>
        <ChevronRight aria-hidden className={cn('h-3.5 w-3.5 shrink-0', open && 'rotate-90')} />
        <span role="status" className="shrink-0">{hookStateLabel(row)}</span>
        <span className="text-ink" title={(hookPhases[row.event as keyof typeof hookPhases] ?? row.event)}>{row.event}</span>
        <span className="shrink-0 text-faint">{t("ui.components.chat.blocks.HookActivity.79f326be4409")}</span>
        <span className="min-w-0 break-words text-xs text-faint">{row.label}</span>
      </> : <>
        <Icon aria-hidden className={cn('h-3 w-3 shrink-0', running && 'animate-spin')} />
        <span className="min-w-0 break-words">{row.label}<span className="ml-2 text-2xs text-faint">{(hookPhases[row.event as keyof typeof hookPhases] ?? row.event)}</span></span>
        <span role="status" className="shrink-0 text-2xs">{hookStateLabel(row)}</span><ChevronRight aria-hidden className={cn('h-3 w-3 shrink-0', open && 'rotate-90')} />
      </>}
    </button>
    {impact ? <p className="ml-6 text-2xs text-muted">{impact}</p> : null}
    {row.reasonCopy || row.reason ? <p className={cn('ml-6 whitespace-pre-wrap break-words text-2xs', issue ? 'text-danger' : 'text-faint')}>{row.reasonCopy ? noticeText(row.reasonCopy) : row.reason}{!row.reasonCopy && row.reason.length === 320 ? t("ui.components.chat.blocks.HookActivity.09de3854c303") : ''}</p> : null}
    {open ? <div id={'hook-detail-' + row.id} className="ml-6 mt-2 space-y-2 rounded-lg border border-line bg-surface/60 p-3 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-faint">{running ? t("ui.components.chat.blocks.HookActivity.5e71231fb4ce") : t("ui.components.chat.blocks.HookActivity.4edf9bba657c", {p0: (formatDuration(row.duration_ms))})}{t("ui.components.chat.blocks.HookActivity.87f1dbacf4ca")}{row.source_seq}</span>
        <button type="button" className="text-brand hover:underline" onClick={() => useAppStore.getState().setSettingsOpen(true, 'hooks', row.root)}>{t("ui.components.chat.blocks.HookActivity.7f7b7132de28")}</button></div>
      {!sessionId || row.session_id !== sessionId ? <p role="alert" className="text-danger">{t("ui.components.chat.blocks.HookActivity.4accd7bf279e")}</p>
        : current?.error ? <div role="alert"><p className="break-words text-danger">{noticeText(current.error)}</p><button type="button" className="mt-1 text-brand hover:underline" onClick={() => setRetry((value) => value + 1)}>{t("ui.components.chat.blocks.HookActivity.c05648b280a9")}</button></div>
        : !detail ? <p role="status" className="flex items-center gap-2 text-muted"><Loader2 aria-hidden className="h-3 w-3 animate-spin" />{t("ui.components.chat.blocks.HookActivity.3022ecf742e8")}</p>
        : <>
          <div className="flex items-center justify-between gap-2"><span className="text-faint">{t("ui.components.chat.blocks.HookActivity.be12f84c407f")}</span><CopyButton text={detail.command} /></div>
          <pre tabIndex={0} className="max-h-32 overflow-auto whitespace-pre-wrap break-all font-mono text-xs text-muted">{detail.command}</pre>
          <p className="break-all text-2xs text-faint">{t("ui.components.chat.blocks.HookActivity.b0e578dd78b1")}{detail.root}</p>
          {detail.reason && detail.reason !== row.reason ? <p className="whitespace-pre-wrap break-words text-danger">{detail.reason}</p> : null}
          {(['stdout', 'stderr'] as const).map((name) => detail[name] ? <div key={name}><div className="flex items-center justify-between text-faint"><span>{name}</span><CopyButton text={detail[name]} /></div><pre tabIndex={0} className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded bg-raised p-2 font-mono text-xs leading-5 text-muted">{detail[name]}</pre></div> : null)}
          {(detail.stdout_dropped ?? 0) + (detail.stderr_dropped ?? 0) > 0 ? <p className="text-2xs text-muted">{t('ui.hooks.outputDropped', {stdout: detail.stdout_dropped ?? 0, stderr: detail.stderr_dropped ?? 0})}</p> : null}
          {!running && !detail.stdout && !detail.stderr ? <p className="text-2xs text-faint">{t("ui.components.chat.blocks.HookActivity.14b68bdf980f")}</p> : null}
        </>}
    </div> : null}
  </article>
}
