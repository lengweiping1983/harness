import { memo, useMemo } from 'react'
import { AlertTriangle, ClipboardCheck } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { splitTeamOutput, type TeamReport } from '@/lib/teamReport'
import { Markdown } from '@/components/markdown/Markdown'

export const TeamOutput = memo(function TeamOutput({ text, streaming = false }: { text: string; streaming?: boolean }) {
  useLocale()
  const parts = useMemo(() => streaming ? null : splitTeamOutput(text), [text, streaming])
  if (!parts) return <Markdown streaming>{text}</Markdown>
  return <>{parts.map((part, index) => part.kind === 'markdown'
    ? <Markdown key={index}>{part.text}</Markdown>
    : <TeamReportCard key={index} report={part.report} code={part.code} />)}</>
})

function TeamReportCard({ report, code }: { report: TeamReport; code: string }) {
  const failed = report.checks.some(check => !check.passed)
    || ['fail', 'failed', 'failure', 'error', 'blocked', 'rejected'].includes(report.status.trim().toLowerCase())
  const passed = report.checks.filter(check => check.passed).length
  return <details open={failed || undefined} className="my-3 overflow-hidden rounded-xl border border-line bg-raised/40 text-sm">
    <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2.5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand">
      {failed ? <AlertTriangle aria-hidden className="h-4 w-4 text-warn" /> : <ClipboardCheck aria-hidden className="h-4 w-4 text-muted" />}
      <span className="font-medium text-ink">{t('ui.team.report.title')}</span>
      <span className={failed ? 'text-warn' : 'text-muted'}>{t(failed ? 'ui.team.report.failed' : 'ui.team.report.count', { count: passed, total: report.checks.length })}</span>
      <span className="ml-auto text-xs text-muted">{t('ui.team.report.expand')}</span>
    </summary>
    <div className="space-y-3 border-t border-line px-3 py-3">
      <p className="text-xs leading-relaxed text-muted">{t('ui.team.report.disclaimer')}</p>
      <ul className="!m-0 !list-none !p-0">
        {report.checks.map((check, index) => <li key={index} className="!m-0 border-b border-line py-2 last:border-0">
          <p className="flex flex-wrap items-baseline gap-x-2 text-sm font-medium text-ink [overflow-wrap:anywhere]">
            <span className={check.passed ? 'text-muted' : 'text-warn'}>{t(check.passed ? 'ui.team.report.checkPassed' : 'ui.team.report.checkFailed')}</span>
            {check.name}
          </p>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-muted [overflow-wrap:anywhere]">{check.detail}</p>
        </li>)}
      </ul>
      {report.evidence.length > 0 && <div className="space-y-1 text-xs text-muted">
        <p className="font-medium">{t('ui.team.report.evidence')}</p>
        {report.evidence.map((item, index) => <p key={index} className="whitespace-pre-wrap [overflow-wrap:anywhere]">{item}</p>)}
      </div>}
      <details className="text-xs text-muted">
        <summary className="cursor-pointer">{t('ui.team.report.raw')}</summary>
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-canvas p-3 font-mono text-2xs">{code}</pre>
      </details>
    </div>
  </details>
}
