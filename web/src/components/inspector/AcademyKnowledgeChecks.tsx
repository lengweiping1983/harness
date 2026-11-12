import { useId, useRef, useState } from 'react'
import { t, useLocale } from '@/i18n'
import type { AcademyKnowledgeCheck } from '@/types'
import { Button, Section } from '@/components/ui/primitives'

function KnowledgeCheck({check, disabled, onChecked, onAsk, asking}: {check: AcademyKnowledgeCheck; disabled: boolean; asking?: boolean; onAsk?: (check: string, option: string) => void; onChecked?: (id: string, answer: string) => void}) {
  const name = useId()
  const [choice, setChoice] = useState('')
  const [answer, setAnswer] = useState('')
  const [hintCount, setHintCount] = useState(0)
  const composing = useRef(false)
  const selected = check.options.find(option => option.id === answer)
  const correct = selected?.id === check.answer_id
  return <form data-academy-check-id={check.id} className="space-y-3 rounded-xl border border-line p-3" onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
    onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}
    onSubmit={event => {event.preventDefault(); if (!composing.current && !disabled && choice) {setAnswer(choice); onChecked?.(check.id, choice)}}}>
    <fieldset disabled={disabled} className="space-y-2">
      <legend className="mb-2 whitespace-pre-wrap text-base leading-reading">{check.prompt}</legend>
      {check.options.map(option => <label key={option.id} className="flex cursor-pointer items-start gap-2 rounded-lg border border-line p-3 text-sm leading-reading focus-within:border-brand">
        <input type="radio" name={name} value={option.id} checked={choice === option.id} className="mt-1 accent-brand" onChange={() => {setChoice(option.id); setAnswer(''); onChecked?.(check.id, '')}} />
        <span className="min-w-0 break-words">{option.label}</span>
      </label>)}
    </fieldset>
    <div className="flex flex-wrap gap-2">
      <Button type="submit" size="sm" disabled={disabled || !choice}>{t('ui.academy.checkUnderstanding')}</Button>
      {hintCount < check.hints.length ? <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={() => setHintCount(count => count + 1)}>{t('ui.academy.showHint', {number: hintCount + 1, total: check.hints.length})}</Button> : null}
    </div>
    {hintCount > 0 ? <ol className="space-y-2 text-sm leading-reading text-muted">{check.hints.slice(0, hintCount).map((hint, index) => <li key={index}>{t('ui.academy.hintEntry', {number: index + 1, hint})}</li>)}</ol> : null}
    {selected ? <div role="status" className="space-y-1 rounded-lg bg-canvas p-3 text-sm leading-reading">
      <p className={correct ? 'font-medium text-ok' : 'font-medium text-warn'}>{t(correct ? 'ui.academy.understandingCorrect' : 'ui.academy.understandingRetry')}</p>
      <p>{selected.feedback}</p>
      {!correct && onAsk ? <Button type="button" size="sm" variant="ghost" disabled={disabled || asking} onClick={() => {if (!composing.current) onAsk(check.id, selected.id)}}>{t('ui.academy.tutor.quizHelp')}</Button> : null}
    </div> : null}
  </form>
}

export function AcademyKnowledgeChecks({checks, disabled, onChecked, onAsk, asking, embedded = false}: {checks: AcademyKnowledgeCheck[]; disabled: boolean; embedded?: boolean; asking?: boolean; onAsk?: (check: string, option: string) => void; onChecked?: (id: string, answer: string) => void}) {
  useLocale()
  return <Section title={embedded ? undefined : t('ui.academy.understanding')}>
    {!embedded ? <p className="mb-3 text-xs leading-reading text-muted">{t('ui.academy.understandingHint')}</p> : null}
    <div className="space-y-3">{checks.map(check => <KnowledgeCheck key={check.id} check={check} disabled={disabled} onChecked={onChecked} onAsk={onAsk} asking={asking} />)}</div>
  </Section>
}
