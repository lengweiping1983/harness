import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import {useEffect, useId, useRef} from 'react'
import {t, useLocale} from '@/i18n'
import {Button} from '@/components/ui/primitives'
import {ApiError, isUnconfirmedWrite} from '@/lib/http'
import {authoringText, submitAuthoring, type AuthoringKind} from '@/lib/agentAuthoring'
import {guardAgentAuthoring, useAgentAuthoring} from '@/store/agentAuthoring'
import {useAppStore} from '@/store/app'

export function AgentCreator({kind, sessionId, disabled = false}: {kind: AuthoringKind; sessionId: string | null; disabled?: boolean}) {
  useLocale()
  const hint = useId()
  const key = JSON.stringify([kind, sessionId])
  const draft = useAgentAuthoring(state => state.drafts[key])
  const update = useAgentAuthoring(state => state.update)
  const locked = disabled || !!draft?.pending || !sessionId
  const visible = useRef(0)
  useEffect(() => {guardAgentAuthoring()}, [])
  useEffect(() => {
    const owner = ++visible.current
    return () => {if (visible.current === owner) visible.current++}
  }, [key])
  const reveal = () => {
    const app = useAppStore.getState()
    if (app.activeSessionId === sessionId) app.setSettingsOpen(false)
  }
  return <form aria-label={t('ui.authoring.title.' + kind)} className="space-y-3 rounded-xl border border-brand/30 bg-brand/5 p-4" onSubmit={event => {
    event.preventDefault()
    const current = useAgentAuthoring.getState().drafts[key]
    if (locked || current?.pending || !current?.requirements.trim() || current.run || !sessionId) return
    const submission = current.submission ?? {id: crypto.randomUUID(), session: sessionId, text: authoringText(kind, current.requirements)}
    const owner = visible.current
    update(key, {submission, pending: true, error: ''})
    void submitAuthoring(submission, !!current.submission).then(run => {
      update(key, {run, pending: false, error: ''})
      const app = useAppStore.getState()
      if (visible.current === owner && app.activeSessionId === sessionId) app.setSettingsOpen(false)
    }).catch(failure => {
      const rejected = failure instanceof ApiError && !isUnconfirmedWrite(failure.failure)
      update(key, {pending: false, error: failureText(failure), ...(rejected ? {submission: undefined} : {})})
    })
  }}>
    <div><h4 className="text-sm font-medium text-ink">{t('ui.authoring.title.' + kind)}</h4><p id={hint} className="mt-1 text-xs leading-relaxed text-muted">{t('ui.authoring.hint')}</p></div>
    {!sessionId ? <p role="status" className="text-xs text-muted">{t('ui.authoring.chooseChat')}</p> : null}
    <label className="block space-y-1 text-xs text-muted"><span>{t('ui.authoring.requirements')}</span><textarea aria-label={t('ui.authoring.requirements')} aria-describedby={hint} className="ui-field min-h-24 w-full resize-y leading-relaxed" value={draft?.requirements ?? ''} maxLength={12000} disabled={locked || !!draft?.submission || !!draft?.run} required onChange={event => update(key, {requirements: event.target.value, error: ''})} /></label>
    {draft?.error ? <p role="alert" className="break-words text-xs text-warn">{noticeText(draft.error)}</p> : null}
    {draft?.run ? <p role="status" className="text-xs text-muted">{t('ui.authoring.accepted')}</p> : null}
    <div className="flex flex-wrap gap-2">
      {draft?.run ? <><Button size="sm" onClick={reveal}>{t('ui.authoring.viewChat')}</Button><Button size="sm" variant="ghost" disabled={locked} onClick={() => useAgentAuthoring.getState().discard(key)}>{t('ui.authoring.another')}</Button></> : <>
        <Button size="sm" type="submit" loading={!!draft?.pending} disabled={locked || !draft?.requirements.trim()}>{t(draft?.submission ? 'ui.authoring.check' : 'ui.authoring.start')}</Button>
        {draft?.requirements && !draft?.submission ? <Button size="sm" variant="ghost" disabled={locked} onClick={() => useAgentAuthoring.getState().discard(key)}>{t('ui.skills.discardDraft')}</Button> : null}
      </>}
    </div>
  </form>
}
