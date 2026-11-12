import { t } from '@/i18n'
import type { RuntimeJob } from '@/types'

export function mergeJobs(older: RuntimeJob[], newer: RuntimeJob[]): RuntimeJob[] {
  const rows = new Map(older.map(job => [job.id, job]))
  for (const job of newer) {
    const previous = rows.get(job.id)
    // An older page cannot turn a confirmed final handle back into a live job.
    // A cold unavailable snapshot remains authoritative about missing ownership.
    if (previous && ['completed', 'killed', 'failed'].includes(previous.status) &&
        !job.archived && ['running', 'queued', 'stopping', 'cleanup-failed'].includes(job.status)) continue
    rows.set(job.id, job)
  }
  return [...rows.values()]
}

export function backgroundJobId(display: unknown): string | null {
  if (!display || typeof display !== 'object') return null
  const value = display as Record<string, unknown>
  const job = value.job && typeof value.job === 'object' ? value.job as Record<string, unknown> : null
  const id = value.background === true ? value.job_id : job?.id
  return typeof id === 'string' && /^job_[a-zA-Z0-9_]{1,100}$/.test(id) ? id : null
}

export function jobPresentation(job: RuntimeJob) {
  const unavailable = job.status === 'unavailable'
  const done = unavailable || job.archived === true || ['completed', 'killed', 'failed'].includes(job.status)
  const terminalWait = job.kind === 'pty-send' || job.completionScope === 'terminal-wait'
  const failed = job.status === 'failed' || job.status === 'cleanup-failed' || !terminalWait && job.status === 'completed' &&
    (typeof job.exitCode === 'number' ? job.exitCode !== 0 : /^exit code: -?[1-9]\d*$/.test(job.detail ?? ''))
  const label = unavailable ? t("ui.lib.jobHistory.1ef634ec346b") : ({ running: terminalWait ? t("ui.lib.jobHistory.7bec72965cec") : t("ui.lib.jobHistory.5026a63b58cd"), queued: t("ui.lib.jobHistory.d6f766f2adf5"), stopping: t("ui.lib.jobHistory.af48159f380f"), 'cleanup-failed': t("ui.lib.jobHistory.601747ffd99d"), completed: terminalWait ? t("ui.lib.jobHistory.8594f46ca513") : failed ? t("ui.lib.jobHistory.6eb1141858cf") : t("ui.lib.jobHistory.f28461bb49c8"), killed: t("ui.lib.jobHistory.f006455e3baf"), failed: t("ui.lib.jobHistory.28384d7afd2e") } as Record<string, string>)[job.status] ?? job.status
  return { unavailable, done, failed, terminalWait, label }
}
