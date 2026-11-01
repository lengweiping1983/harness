import {useRef, useState, type Ref} from 'react'
import {Play} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import type {AcademyLesson, AcademyModelRun} from '@/types'
import {Button, RefreshButton, Section, Spinner} from '@/components/ui/primitives'
import {createBrowserDraftStorage} from '@/lib/browserDraftStorage'
import {isMac} from '@/lib/utils'
import {AcademyModelComparison} from './AcademyModelComparison'

const draftKey = (project: string, lesson: string) => 'harness.academy.model-draft.v1:' + JSON.stringify([project, lesson])
const draftStores = new Map<string, ReturnType<typeof createDraftStore>>()

/** 每个项目课次保留自己的未保存意图；重开组件不能恢复过时磁盘文本。 */
function createDraftStore(key: string) {
  let failed = false
  let unpersisted = false
  const storage = createBrowserDraftStorage(() => sessionStorage, () => {failed = true}, raw => {
    try {const value: unknown = JSON.parse(raw); return typeof value === 'string' && [...value].length <= 8000}
    catch {return false}
  })
  return {
    read() {
      failed = false
      const raw = storage.getItem(key)
      return {value: raw === null ? null : JSON.parse(raw) as string, failed: failed || unpersisted}
    },
    write(value: string | null) {
      failed = false
      if (value === null) storage.removeItem(key)
      else storage.setItem(key, JSON.stringify(value))
      unpersisted = failed
      return !failed
    },
  }
}

/** 提示词按项目和课次保存，语言切换保留用户编辑及模型原文。 */
export function AcademyModelLab({projectId, lesson, runs, disabled, asking, onRun, onRefresh, onAsk, onStop, promptRef}: {
  projectId: string
  lesson: AcademyLesson
  runs: AcademyModelRun[]
  disabled: boolean
  asking: boolean
  promptRef?: Ref<HTMLTextAreaElement>
  onRun: (prompt: string, returnFocus?: boolean) => void
  onRefresh: () => void
  onStop: (run: AcademyModelRun) => void
  onAsk: (runId: string, comparisonId?: string) => void
}) {
  useLocale()
  const key = draftKey(projectId, lesson.id)
  const [storage] = useState(() => {
    let store = draftStores.get(key)
    if (!store) {store = createDraftStore(key); draftStores.set(key, store)}
    return store
  })
  const [initial] = useState(() => storage.read())
  const [edited, setEdited] = useState<string | null>(initial.value)
  const [storageError, setStorageError] = useState(initial.failed)
  const [selectedId, setSelectedId] = useState('')
  const composing = useRef(false)
  const prompt = edited ?? lesson.model_prompt ?? ''
  const history = runs.filter(run => run.project_id === projectId && run.lesson_id === lesson.id)
  const selected = history.find(run => run.id === selectedId) ?? history[0]
  const running = history.find(run => run.status === 'running')
  const locked = disabled || !!running || storageError || !prompt.trim()
  const run = (returnFocus = false) => {if (!locked && !composing.current) {setSelectedId(''); onRun(prompt, returnFocus)}}
  const shortcut = t('ui.academy.modelShortcut', {shortcut: isMac() ? '⌘↵' : 'Ctrl+Enter'})
  const save = (value: string | null) => {
    setEdited(value)
    setStorageError(!storage.write(value))
  }
  return <Section title={t('ui.academy.modelLab')}>
    <p className="text-sm leading-reading text-muted">{t('ui.academy.modelHint')}</p>
    <label className="mt-3 block space-y-2 text-sm"><span>{t('ui.academy.modelPrompt')}</span><textarea
      ref={promptRef} aria-label={t('ui.academy.modelPrompt')} title={shortcut} value={prompt} maxLength={8000} rows={7} disabled={disabled}
      className="ui-field w-full resize-y leading-reading" onChange={event => save(event.target.value)}
      onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
      onKeyDown={event => {
        if (event.key !== 'Enter') return
        if (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) {event.stopPropagation(); return}
        if ((!event.ctrlKey && !event.metaKey) || event.shiftKey || event.altKey || event.nativeEvent.getModifierState('AltGraph')) return
        event.preventDefault()
        event.stopPropagation()
        if (!event.repeat) run(true)
      }}
    /></label>
    {storageError ? <div className="mt-2 space-y-2"><p role="alert" className="text-sm text-warn">{t('ui.academy.modelDraftError')}</p><Button size="sm" variant="ghost" disabled={disabled} onClick={() => {if (!composing.current) save(edited)}}>{t('ui.academy.modelDraftRetry')}</Button></div> : null}
    <div className="refresh-action-container mt-3 flex flex-wrap gap-2">
      {running ? <Button size="sm" variant="subtle" disabled={disabled || running.stopping}
        onKeyDown={event => {if (event.key === 'Enter' && (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}
        onClick={() => {if (!composing.current) onStop(running)}}>{t(running.stopping ? 'ui.academy.modelStoppingAction' : 'ui.academy.modelStop')}</Button> : <Button size="sm" variant="primary" disabled={locked} title={shortcut} aria-keyshortcuts="Control+Enter Meta+Enter" icon={<Play className="h-3.5 w-3.5" />}
        onKeyDown={event => {if (event.key === 'Enter' && (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}
        onClick={() => run()}>{t('ui.academy.modelRun')}</Button>}
      <details data-academy-section="model-options" className="text-xs text-muted"><summary className="cursor-pointer rounded-lg px-2 py-1 focus-visible:outline focus-visible:outline-brand">{t('ui.academy.more')}</summary><div className="mt-2 space-y-3">
        <p className="leading-reading">{shortcut}</p>
        <div className="refresh-action-container flex flex-wrap gap-2"><Button size="sm" variant="ghost" disabled={disabled || storageError || edited === null} onClick={() => {if (!composing.current) save(null)}}>{t('ui.academy.modelReset')}</Button>
        <RefreshButton size="sm" variant="ghost" disabled={disabled} label={t('ui.academy.modelRefresh')} onClick={onRefresh} /></div>
        {lesson.model_system ? <div><h4 className="ui-group-title">{t('ui.academy.modelSystem')}</h4><p className="mt-2 whitespace-pre-wrap break-words leading-reading">{lesson.model_system}</p></div> : null}
      </div></details>
    </div>
    {history.length > 1 ? <label className="mt-4 block space-y-1 text-sm"><span>{t('ui.academy.modelHistory')}</span><select className="ui-field w-full" value={selected?.id ?? ''} onChange={event => setSelectedId(event.target.value)}>{history.map((run, index) => <option key={run.id} value={run.id}>{t('ui.academy.modelEntry', {number: history.length - index, status: t('ui.academy.modelStatus.' + run.status)})}</option>)}</select></label> : null}
    {selected ? <div className="mt-4 space-y-3" data-model-run-id={selected.id}>
      <p role="status" className="flex items-center gap-2 text-sm text-muted">{selected.status === 'running' ? <Spinner /> : null}{t(selected.stopping ? 'ui.academy.modelStopping' : 'ui.academy.modelStatus.' + selected.status)}</p>
      {selected.error_code ? <p className="text-sm leading-reading text-warn">{t(['model-unavailable', 'model-timeout', 'model-interrupted', 'model-invalid', 'model-failed', 'model-truncated'].includes(selected.error_code) ? 'ui.academy.error.' + selected.error_code : 'ui.academy.error.model-failed')}</p> : null}
      {selected.prompt !== prompt || selected.system !== lesson.model_system ? <p className="text-xs leading-reading text-warn">{t('ui.academy.modelPrevious')}</p> : null}
      <details data-academy-section="model-evidence" className="text-xs text-muted"><summary className="cursor-pointer rounded-lg py-1 focus-visible:outline focus-visible:outline-brand">{t('ui.academy.modelInput')}</summary><p className="mt-2">{t('ui.academy.modelLanguage', {language: selected.language, model: selected.model})}</p><p className="mt-2 whitespace-pre-wrap break-words leading-reading">{selected.prompt}</p><h4 className="ui-group-title mt-3">{t('ui.academy.modelSystem')}</h4><p className="mt-2 whitespace-pre-wrap break-words leading-reading">{selected.system}</p></details>
      {selected.status === 'running' && !selected.stopping && selected.partial_response ? <div aria-label={t('ui.academy.modelPartial')} aria-busy="true" className="space-y-2"><p className="text-xs text-muted">{t('ui.academy.modelPartial')}</p><div className="whitespace-pre-wrap break-words rounded-xl border border-line bg-canvas p-3 text-base leading-reading">{selected.partial_response}</div></div> : null}
      {selected.response ? <div className="whitespace-pre-wrap break-words rounded-xl border border-line bg-canvas p-3 text-base leading-reading" aria-label={t('ui.academy.modelResponse')}>{selected.response}</div> : null}
      {selected.status === 'completed' ? <div className="space-y-1"><Button size="sm" variant="ghost" disabled={disabled || asking} onClick={() => {if (!composing.current && !asking) onAsk(selected.id)}}>{t('ui.academy.tutor.explainResult')}</Button><p className="text-xs leading-reading text-muted">{t('ui.academy.tutor.modelContext')}</p></div> : null}
      <AcademyModelComparison key={selected.id} run={selected} runs={history} disabled={disabled || asking} onAsk={previous => {if (!composing.current && !asking) onAsk(selected.id, previous.id)}} />
    </div> : <p className="mt-4 text-sm leading-reading text-muted">{t('ui.academy.modelEmpty')}</p>}
  </Section>
}
