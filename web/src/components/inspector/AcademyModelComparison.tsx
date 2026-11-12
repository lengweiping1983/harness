import {useRef, useState} from 'react'
import {t, useLocale} from '@/i18n'
import type {AcademyModelRun} from '@/types'
import {Button} from '@/components/ui/primitives'

function Experiment({run, label}: {run: AcademyModelRun; label: string}) {
  return <section aria-label={label} className="min-w-0 space-y-3 rounded-lg border border-line p-3">
    <h4 className="ui-group-title">{label}</h4>
    <div><p className="mb-1 text-xs text-muted">{t('ui.academy.modelPrompt')}</p><p className="max-h-48 overflow-auto whitespace-pre-wrap break-words text-sm leading-reading">{run.prompt}</p></div>
    <div><p className="mb-1 text-xs text-muted">{t('ui.academy.modelResponse')}</p><p className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-sm leading-reading">{run.response}</p></div>
    <details className="text-xs text-muted"><summary className="cursor-pointer rounded-lg py-1 focus-visible:outline focus-visible:outline-brand">{t('ui.academy.compare.details')}</summary>
      <p className="mt-2 leading-reading">{t('ui.academy.modelLanguage', {language: run.language, model: run.model})}</p>
      <p className="mt-2 break-all font-mono">{run.id}</p>
      <h5 className="ui-group-title mt-3">{t('ui.academy.modelSystem')}</h5><p className="mt-2 whitespace-pre-wrap break-words leading-reading">{run.system}</p>
    </details>
  </section>
}

/** 对照按已保存的先后顺序选取；展开、改选和阅读不发起模型调用。 */
export function AcademyModelComparison({run, runs, disabled, onAsk}: {
  run: AcademyModelRun; runs: readonly AcademyModelRun[]; disabled: boolean; onAsk: (comparison: AcademyModelRun) => void
}) {
  useLocale()
  const [selected, setSelected] = useState('')
  const composing = useRef(false)
  const owned = runs.filter(item => item.project_id === run.project_id && item.lesson_id === run.lesson_id)
  const index = owned.findIndex(item => item.id === run.id)
  const candidates = index < 0 ? [] : owned.slice(index + 1).filter(item => item.status === 'completed').slice(0, 10)
  const previous = candidates.find(item => item.id === selected) ?? candidates[0]
  if (run.status !== 'completed' || !previous) return null
  const sameInput = previous.prompt === run.prompt && previous.system === run.system && previous.model === run.model && previous.language === run.language
  return <details data-academy-section="model-comparison" className="mt-3 rounded-lg border border-line p-3"
    onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
    onKeyDown={event => {if (event.key === 'Enter' && (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}>
    <summary className="cursor-pointer rounded-lg text-sm text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.modelCompare.title')}</summary>
    <div className="mt-3 space-y-3">
      <p className="text-sm leading-reading">{t(previous.response === run.response ? 'ui.academy.modelCompare.sameResponse' : 'ui.academy.modelCompare.changedResponse')}</p>
      <p className="text-xs leading-reading text-muted">{t(sameInput ? 'ui.academy.modelCompare.sameInput' : 'ui.academy.modelCompare.changedInput')}</p>
      {candidates.length > 1 ? <label className="block space-y-1 text-sm"><span>{t('ui.academy.modelCompare.choose')}</span><select className="ui-field w-full" value={previous.id} onChange={event => setSelected(event.target.value)}>{candidates.map((item, position) => <option key={item.id} value={item.id}>{t('ui.academy.modelCompare.entry', {number: position + 1})}</option>)}</select></label> : null}
      <div className="space-y-2"><Button size="sm" variant="subtle" disabled={disabled} onClick={() => {if (!composing.current) onAsk(previous)}}>{t('ui.academy.compare.ask')}</Button><p className="text-xs leading-reading text-muted">{t('ui.academy.modelCompare.hint')}</p></div>
      <div className="academy-run-comparison"><div className="academy-run-comparison-grid">
        <Experiment key={'before:' + previous.id} run={previous} label={t('ui.academy.compare.before')} />
        <Experiment key={'after:' + run.id} run={run} label={t('ui.academy.compare.after')} />
      </div></div>
    </div>
  </details>
}
