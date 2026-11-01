import {useRef, useState, type Ref} from 'react'
import {MessageCircle, Play, Send} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import type {AcademyLesson, AcademyTutorProposal, AcademyTutorTurn} from '@/types'
import {Button, RefreshButton, Section} from '@/components/ui/primitives'
import {DiffView} from '@/components/chat/blocks/DiffView'
import {Markdown} from '@/components/markdown/Markdown'
import {createBrowserDraftStorage} from '@/lib/browserDraftStorage'
import {AcademyTutorStatus} from './AcademyTutorStatus'

const stores = new Map<string, ReturnType<typeof draftStore>>()
function draftStore(key: string) {
  let failed = false
  let unsaved = false
  const storage = createBrowserDraftStorage(() => sessionStorage, () => {failed = true}, raw => {
    try {const value: unknown = JSON.parse(raw); return typeof value === 'string' && [...value].length <= 4000}
    catch {return false}
  })
  return {
    read() {failed = false; const raw = storage.getItem(key); return {text: raw === null ? '' : JSON.parse(raw) as string, failed: failed || unsaved}},
    write(text: string) {failed = false; storage.setItem(key, JSON.stringify(text)); unsaved = failed; return !failed},
  }
}

/** 课内提问与代码共用所属项目；语言切换只更新控件，不改写问答原文。 */
export function AcademyTutor({projectId, lesson, fingerprint, dirty, turns, disabled, questionRef, proposalPath, onSuggest, onTry, onAsk, onStop, onRefresh}: {
  projectId: string; lesson: AcademyLesson; fingerprint: string; dirty: boolean; turns: AcademyTutorTurn[]; disabled: boolean
  questionRef?: Ref<HTMLTextAreaElement>; proposalPath?: string; onSuggest: () => void; onTry: (turn: AcademyTutorTurn, proposal: AcademyTutorProposal) => void
  onStop: (turn: AcademyTutorTurn) => void; onAsk: (prompt: string, allowExecution: boolean, requireExecution: boolean) => void; onRefresh: () => void
}) {
  useLocale()
  const [storage] = useState(() => {
    const key = 'harness.academy.tutor-draft.v1:' + JSON.stringify([projectId, lesson.id])
    let store = stores.get(key)
    if (!store) {store = draftStore(key); stores.set(key, store)}
    return store
  })
  const [initial] = useState(() => storage.read())
  const [prompt, setPrompt] = useState(initial.text)
  const [storageError, setStorageError] = useState(initial.failed)
  const [allow, setAllow] = useState(false)
  const composing = useRef(false)
  const history = turns.filter(turn => turn.project_id === projectId && turn.lesson_id === lesson.id)
  const latest = history[0]
  const locked = disabled || history.some(turn => turn.status === 'running') || storageError
  const ask = (text: string, execute: boolean, required = false) => {if (!locked && !composing.current && text.trim()) onAsk(text, execute, required)}
  const exchange = (turn: AcademyTutorTurn) => <article key={turn.id} data-tutor-turn-id={turn.id} className="space-y-3">
    <p className="whitespace-pre-wrap break-words rounded-lg bg-canvas p-3 text-sm leading-reading">{turn.prompt}</p>
    <AcademyTutorStatus turn={turn} />
    {turn.error_code ? <p role="status" className="text-sm text-warn">{t(['tutor-interrupted', 'tutor-timeout', 'tutor-failed', 'tutor-limit', 'tutor-execution-required', 'tutor-proposal-required', 'outcome-unknown'].includes(turn.error_code) ? 'ui.academy.error.' + turn.error_code : 'ui.academy.error.tutor-failed')}</p> : null}
    {turn.status === 'running' && !turn.stopping && turn.partial_response ? <div aria-label={t('ui.academy.tutor.partial')} aria-busy="true" className="space-y-2 text-base leading-reading"><p className="text-xs text-muted">{t('ui.academy.tutor.partial')}</p><Markdown workspaceLinks={false}>{turn.partial_response}</Markdown></div> : null}
    {turn.input_fingerprint !== fingerprint || dirty ? <p className="text-xs text-warn">{t('ui.academy.tutor.previous')}</p> : null}
    {turn.actions.filter(action => action.proposal).map((action, index) => <section key={index} aria-label={t('ui.academy.tutor.proposal')} className="space-y-3 rounded-lg border border-line p-3">
      <p className="text-sm leading-reading text-muted">{t('ui.academy.tutor.proposalHint')}</p>
      <DiffView output={action.proposal} path={action.proposal!.path} />
      <Button size="sm" variant="subtle" disabled={locked || dirty || turn.status !== 'completed' || turn.input_fingerprint !== fingerprint}
        onClick={() => {if (!composing.current) onTry(turn, action.proposal!)}}>{t('ui.academy.tutor.tryProposal')}</Button>
    </section>)}
    {turn.response ? <div aria-label={t('ui.academy.tutor.answer')} className="text-base leading-reading"><Markdown workspaceLinks={false}>{turn.response}</Markdown></div> : null}
    <details data-academy-section="tutor-evidence" className="text-xs text-muted"><summary className="cursor-pointer rounded-lg py-1 focus-visible:outline focus-visible:outline-brand">{t('ui.academy.tutor.evidence')}</summary><div className="mt-2 space-y-2">{turn.evidence_run_id ? <p className="break-all text-xs text-muted">{t('ui.academy.tutor.resultEvidence', {id: turn.evidence_run_id})}</p> : null}
    {turn.knowledge_check ? <p className="break-all text-xs text-muted">{t('ui.academy.tutor.quizEvidence', {question: turn.knowledge_check.check_id, choice: turn.knowledge_check.option_id})}</p> : null}
    {turn.comparison_model_run_id ? <p className="break-all text-xs text-muted">{t('ui.academy.modelCompare.evidence', {id: turn.comparison_model_run_id})}</p> : null}
    {turn.model_run_id ? <p className="break-all text-xs text-muted">{t('ui.academy.tutor.modelEvidence', {id: turn.model_run_id})}</p> : null}
    {turn.comparison_run_id ? <p className="break-all text-xs text-muted">{t('ui.academy.tutor.comparisonEvidence', {id: turn.comparison_run_id})}</p> : null}
    {turn.actions.length ? <details className="text-sm text-muted"><summary className="cursor-pointer rounded-lg py-1 focus-visible:outline focus-visible:outline-brand">{t('ui.academy.tutor.actions')}</summary>
      <ol className="mt-2 space-y-3">{turn.actions.map((action, index) => <li key={index}>
        <p className="flex flex-wrap gap-2"><span>{t('ui.academy.tutor.action.' + action.tool)}</span><span>{t('ui.academy.tutor.actionStatus.' + action.status)}</span>{action.path ? <code className="break-all">{action.path}</code> : null}</p>
        {action.output ? <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-canvas p-3 font-mono text-xs leading-code">{action.output}</pre> : null}
      </li>)}</ol>
    </details> : null}    <p className="text-xs text-muted">{t('ui.academy.modelLanguage', {model: turn.model, language: turn.language})}</p></div></details>
  </article>
  return <Section title={t('ui.academy.tutor.title')} right={<MessageCircle className="h-4 w-4 text-brand" />} className="max-w-prose">
    {latest ? <div className="my-3">{exchange(latest)}</div> : null}
    <form className="space-y-3" onSubmit={event => {event.preventDefault(); ask(prompt, lesson.mode === 'python' && allow)}}
      onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
      onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}>
      <label className="block space-y-2 text-sm"><span className="sr-only">{t('ui.academy.tutor.question')}</span><textarea ref={questionRef} className="ui-field w-full resize-y leading-reading" rows={3} maxLength={4000} value={prompt}
        aria-label={t('ui.academy.tutor.question')} placeholder={t('ui.academy.tutor.placeholder')} onKeyDown={event => {
          if (event.key !== 'Enter') return
          if (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) {event.preventDefault(); return}
          if (event.shiftKey || event.altKey || event.nativeEvent.getModifierState('AltGraph')) return
          event.preventDefault()
          event.stopPropagation()
          if (!event.repeat) ask(prompt, lesson.mode === 'python' && allow)
        }} onChange={event => {setPrompt(event.target.value); setStorageError(!storage.write(event.target.value))}} /></label>
      {allow ? <div className="flex flex-wrap items-center gap-2 text-xs text-muted"><span>{t('ui.academy.tutor.executionAllowed')}</span><Button size="xs" variant="ghost" disabled={locked} onClick={() => {if (!composing.current) setAllow(false)}}>{t('ui.academy.tutor.disableExecution')}</Button></div> : null}
      {storageError ? <div className="space-y-2"><p role="alert" className="text-sm text-warn">{t('ui.academy.modelDraftError')}</p><Button size="xs" variant="ghost" onClick={() => {if (!composing.current) setStorageError(!storage.write(prompt))}}>{t('ui.academy.modelDraftRetry')}</Button></div> : null}
      {latest?.status === 'running' ? <Button size="sm" variant="subtle" disabled={disabled || latest.stopping} onClick={() => {if (!composing.current) onStop(latest)}}>{t(latest.stopping ? 'ui.academy.tutor.stoppingAction' : 'ui.academy.tutor.stop')}</Button> : <><Button type="submit" size="sm" variant="primary" disabled={locked || !prompt.trim()} icon={<Send className="h-3.5 w-3.5" />}>{t(dirty ? 'ui.academy.tutor.saveAndSend' : 'ui.academy.tutor.send')}</Button></>}
      <p className="text-xs text-muted">{t('ui.academy.tutor.cost')}</p>
      <details data-academy-section="tutor-options" className="text-xs text-muted"><summary className="cursor-pointer rounded-lg py-1 focus-visible:outline focus-visible:outline-brand">{t('ui.academy.tutor.more')}</summary><div className="mt-3 space-y-3">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="ghost" disabled={locked} onClick={() => ask(t('ui.academy.tutor.hintPrompt'), false)}>{t('ui.academy.tutor.giveHint')}</Button>
          {lesson.mode === 'python' && proposalPath ? <Button size="sm" variant="ghost" disabled={locked} onClick={() => {if (!composing.current) onSuggest()}}>{t(dirty ? 'ui.academy.tutor.saveAndSuggest' : 'ui.academy.tutor.suggest')}</Button> : null}
          {lesson.mode === 'python' ? <Button size="sm" variant="ghost" disabled={locked} icon={<Play className="h-3.5 w-3.5" />} onClick={() => ask(t('ui.academy.tutor.runPrompt'), true, true)}>{t('ui.academy.tutor.runExplain')}</Button> : null}
        </div>
        {lesson.mode === 'python' ? <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={allow} disabled={locked} onChange={event => setAllow(event.target.checked)} />{t('ui.academy.tutor.allow')}</label> : null}
        <RefreshButton size="xs" variant="ghost" label={t('ui.academy.tutor.refresh')} disabled={disabled} onClick={onRefresh} />
        <p className="leading-reading">{t('ui.academy.tutor.scope')}</p>
      </div></details>
    </form>
    {history.length > 1 ? <details className="mt-3 text-sm"><summary className="cursor-pointer rounded-lg py-1 text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.tutor.history')}</summary><div className="mt-3 space-y-5">{history.slice(1, 5).reverse().map(exchange)}</div></details> : null}
  </Section>
}
