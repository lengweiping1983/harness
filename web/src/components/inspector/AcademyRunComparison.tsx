import {useState} from 'react'
import {t, useLocale} from '@/i18n'
import {Button} from '@/components/ui/primitives'
import type {AcademyRun} from '@/types'
import {academyCapturedOutputMatches, academyComparisonRuns} from '@/lib/academyRunComparison'

function RunEvidence({run, label}: {run: AcademyRun; label: string}) {
  const status = t(run.result.status === 'passed' ? 'ui.academy.runPassed' : run.result.status === 'timeout' ? 'ui.academy.runTimeout' : 'ui.academy.runFailed')
  return <section aria-label={label} className="min-w-0 space-y-3 rounded-lg border border-line p-3">
    <h4 className="ui-group-title">{label}</h4>
    <p className="text-sm text-muted">{status}</p>
    {run.result.stdout ? <div><p className="mb-1 text-xs text-muted">{t('ui.academy.compare.stdout')}</p><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-canvas p-3 font-mono text-xs leading-code">{run.result.stdout}</pre></div> : null}
    {run.result.stderr ? <div><p className="mb-1 text-xs text-muted">{t('ui.academy.compare.stderr')}</p><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-canvas p-3 font-mono text-xs leading-code">{run.result.stderr}</pre></div> : null}
    {!run.result.stdout && !run.result.stderr ? <p className="text-sm text-muted">{t('ui.academy.noOutput')}</p> : null}
    <p className="text-xs text-muted">{run.result.exit_code === null ? t('ui.academy.compare.exitUnknown') : t('ui.academy.compare.exitCode', {code: run.result.exit_code})}</p>
    {run.input_changed ? <p className="text-xs leading-reading text-warn">{t('ui.academy.inputChanged')}</p> : null}
    <details className="text-xs text-muted"><summary className="cursor-pointer rounded-lg py-1 focus-visible:outline focus-visible:outline-brand">{t('ui.academy.compare.details')}</summary>
      <dl className="mt-2 space-y-2"><div><dt>{t('ui.academy.compare.runId')}</dt><dd className="break-all font-mono">{run.id}</dd></div><div><dt>{t('ui.academy.compare.version')}</dt><dd className="break-all font-mono">{run.input_fingerprint}</dd></div></dl>
    </details>
  </section>
}

export function AcademyRunComparison({run, runs, disabled = false, onAsk}: {run: AcademyRun; runs: readonly AcademyRun[]; disabled?: boolean; onAsk?: (comparison: AcademyRun) => void}) {
  useLocale()
  const [selected, setSelected] = useState('')
  const candidates = academyComparisonRuns(runs, run)
  const previous = candidates.find(candidate => candidate.id === selected) ?? candidates[0]
  if (!previous) return null
  return <details className="mt-3 rounded-lg border border-line p-3">
    <summary className="cursor-pointer rounded-lg text-sm text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.compare.title')}</summary>
    <div className="mt-3 space-y-3" aria-label={t('ui.academy.compare.title')}>
      <p className="text-sm leading-reading">{t(academyCapturedOutputMatches(previous, run) ? 'ui.academy.compare.sameOutput' : 'ui.academy.compare.changedOutput')}</p>
      <p className="text-xs leading-reading text-muted">{t(previous.input_fingerprint === run.input_fingerprint ? 'ui.academy.compare.sameCode' : 'ui.academy.compare.changedCode')}</p>
      {run.result.truncated || previous.result.truncated ? <p className="text-xs leading-reading text-warn">{t('ui.academy.compare.truncated')}</p> : null}
      {candidates.length > 1 ? <label className="block space-y-1 text-sm"><span>{t('ui.academy.compare.choose')}</span><select className="ui-field w-full" value={previous.id} onChange={event => setSelected(event.target.value)}>{candidates.map((candidate, index) => <option value={candidate.id} key={candidate.id}>{t('ui.academy.compare.entry', {number: index + 1, version: candidate.input_fingerprint.slice(0, 12)})}</option>)}</select></label> : null}
      {onAsk ? <div className="space-y-2"><Button size="sm" variant="subtle" disabled={disabled} onClick={() => onAsk(previous)}>{t('ui.academy.compare.ask')}</Button><p className="text-xs leading-reading text-muted">{t('ui.academy.compare.askHint')}</p></div> : null}
      <div className="academy-run-comparison"><div className="academy-run-comparison-grid">
        <RunEvidence key={'previous:' + previous.id} run={previous} label={t('ui.academy.compare.before')} />
        <RunEvidence key={'current:' + run.id} run={run} label={t('ui.academy.compare.after')} />
      </div></div>
    </div>
  </details>
}
