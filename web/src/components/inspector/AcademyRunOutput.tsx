import {t, useLocale} from '@/i18n'
import type {AcademyRun} from '@/types'
import {Button} from '@/components/ui/primitives'
import {academyDiagnostic, type AcademySourceLocation} from '@/lib/academyDiagnostics'

export function AcademyRunOutput({run, root, files, canLocate, onLocate}: {
  run: AcademyRun; root: string; files: string[]; canLocate: boolean; onLocate: (location: AcademySourceLocation) => void
}) {
  useLocale()
  const diagnostic = run.result.status === 'failed' ? academyDiagnostic(run.result.stderr, root, files) : null
  const output = <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-canvas p-3 font-mono text-xs leading-code">{run.result.stdout + run.result.stderr || t('ui.academy.noOutput')}</pre>
  return <div data-academy-run-id={run.id} className="mt-2 space-y-3">
    {diagnostic ? <>
      <p role="note" className="whitespace-pre-wrap break-words font-mono text-sm text-danger">{diagnostic.summary}</p>
      {diagnostic.location ? <Button size="sm" variant="ghost" disabled={!canLocate} className="h-auto min-h-8 whitespace-normal break-all text-left"
        onClick={() => onLocate(diagnostic.location!)}>{t('ui.academy.errorLocation', {path: diagnostic.location.path, line: diagnostic.location.line})}</Button> : null}
      {run.result.stdout ? <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-canvas p-3 font-mono text-xs leading-code">{run.result.stdout}</pre> : null}
      <details><summary className="cursor-pointer rounded-lg py-1 text-xs text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.fullRunOutput')}</summary><div className="mt-2">{output}</div></details>
    </> : output}
  </div>
}
