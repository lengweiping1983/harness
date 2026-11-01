import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { useEffect, useRef, useState } from 'react'
import { t, useLocale, getLocale } from '@/i18n'
import { request } from '@/lib/http'
import type { RunRecord } from '@/lib/protocol.generated'

export function ContextCompactControl({ sessionId, onComplete }: { sessionId: string; onComplete: () => void }) {
  useLocale()
  const [task, setTask] = useState<RunRecord | null>(null)
  const [busy, setBusy] = useState(false)
  const [submissionMode, setSubmissionMode] = useState<'compact' | 'project'>('compact')
  const [error, setError] = useState<ResourceText>('')
  const callback = useRef(onComplete)
  callback.current = onComplete
  const generation = useRef(0)
  const pending = useRef(false)
  const submission = useRef<AbortController>()
  const path = `/api/v1/sessions/${encodeURIComponent(sessionId)}/context/compact`
  useEffect(() => {
    const ticket = ++generation.current
    setTask(null); setBusy(false); setError(''); pending.current = false
    const abort = new AbortController()
    void request<RunRecord | null>(path, { signal: abort.signal }).then(result => {
      if (ticket === generation.current && !abort.signal.aborted && result.ok) setTask(result.data)
    })
    return () => { ++generation.current; abort.abort(); submission.current?.abort() }
  }, [path])
  useEffect(() => {
    if (!task || !['queued', 'running'].includes(task.status)) return
    const abort = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      const result = await request<RunRecord | null>(path, { signal: abort.signal })
      if (abort.signal.aborted) return
      if (result.ok && result.data) {
        setTask(result.data)
        if (!['queued', 'running'].includes(result.data.status)) { callback.current(); return }
      }
      timer = setTimeout(() => void poll(), 1000)
    }
    timer = setTimeout(() => void poll(), 500)
    return () => { abort.abort(); clearTimeout(timer) }
  }, [path, task?.id, task?.status])
  async function compact(restart = false, mode: 'compact' | 'project' = 'compact') {
    if (pending.current) return
    const ticket = generation.current
    const abort = new AbortController()
    submission.current = abort
    pending.current = true; setBusy(true); setSubmissionMode(mode); setError('')
    const request_id = crypto.randomUUID()
    const result = await request<RunRecord>(path, { method: 'POST', body: JSON.stringify({ request_id, restart, mode }), timeout: 65000, signal: abort.signal })
    if (abort.signal.aborted || ticket !== generation.current) return
    const check = result.ok ? result : await request<RunRecord | null>(`${path}?request_id=${encodeURIComponent(request_id)}`, { signal: abort.signal })
    if (abort.signal.aborted || ticket !== generation.current) return
    pending.current = false; setBusy(false)
    const expectedText = mode === 'project' ? 'project' : restart ? 'restart' : ''
    if (check.ok && check.data && check.data.text === expectedText) { setTask(check.data); if (!['queued', 'running'].includes(check.data.status)) callback.current() }
    else setError(result.ok ? {key: 'ui.context.saveUnknown'} : result.copy ?? result.message)
  }
  const running = !!task && ['queued', 'running'].includes(task.status)
  const failed = task?.status === 'failed' || task?.status === 'interrupted'
  const projection = task?.text === 'project'
  const exhausted = task?.error?.code === 'agent/compaction-recovery-exhausted'
  const details = task?.error?.details as { completed_chunks?: number; total_chunks?: number } | undefined
  const result = task?.result as { before_tokens?: number; after_tokens?: number; changed?: boolean; projected_results?: number; reason?: string } | null
  const waiting = (project: boolean) => busy && (submissionMode === 'project') === project || task?.status === 'queued' && projection === project
  const working = (project: boolean) => task?.status === 'running' && projection === project
  const completed = projection ? result?.reason === 'projection_disabled' ? t('ui.context.projectDisabled')
    : result?.changed ? t('ui.context.projectCompleted', { count: result.projected_results ?? 0 }) : t('ui.context.projectNothing')
    : result?.changed ? t('ui.context.completed') : t('ui.context.noHistory')
  return <div className="space-y-2">
    <div className="flex flex-wrap gap-2">
      <button type="button" onClick={() => void compact(false, 'project')} disabled={busy || running} className="rounded-lg border border-line px-3 py-2 text-xs font-medium focus-visible:outline-brand disabled:opacity-50">{t(waiting(true) ? 'ui.context.waiting' : working(true) ? 'ui.context.projecting' : failed && projection ? 'ui.context.projectRetry' : 'ui.context.project')}</button>
      <button type="button" onClick={() => void compact(exhausted)} disabled={busy || running} className="rounded-lg border border-line px-3 py-2 text-xs font-medium focus-visible:outline-brand disabled:opacity-50">{t(waiting(false) ? 'ui.context.waiting' : working(false) ? 'ui.context.compacting' : exhausted ? 'ui.context.restart' : failed && !projection ? 'ui.context.recover' : 'ui.context.compact')}</button>
    </div>
    <p className="text-2xs leading-relaxed text-faint">{t('ui.context.projectHint')}</p>
    {exhausted ? <p className="text-2xs text-muted">{t('ui.context.restartHint')}</p> : null}
    {details?.total_chunks ? <p className="text-2xs text-muted">{t('ui.context.recoveryProgress', { completed: details.completed_chunks ?? 0, total: details.total_chunks })}</p> : null}
    {error ? <p role="alert" className="text-xs text-danger">{noticeText(error)}</p> : null}
    {task ? <p role="status" className="text-2xs text-muted">{task.status === 'completed' ? result?.changed ? t('ui.context.completedUsage', {result: completed, before: String(result.before_tokens?.toLocaleString(getLocale())), after: String(result.after_tokens?.toLocaleString(getLocale()))}) : completed : failed ? t('ui.context.failed', {reason: String(task.error?.message ?? '')}) : t(task.status === 'queued' ? 'ui.context.waiting' : projection ? 'ui.context.projecting' : 'ui.context.compacting')}</p> : null}
  </div>
}
