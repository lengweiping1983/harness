import type { ResourceText } from '@/types'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale , getLocale } from '@/i18n'
import { useEffect, useRef, useState } from 'react'
import { ChevronRight, Loader2, Pause, Pencil, Play, Target, Trash2, X } from 'lucide-react'
import type { GoalState } from '@/types'
import { controlGoal } from '@/lib/goalControl'
import { useDraftStore } from '@/store/drafts'
import { useChat } from '@/store/chat'
import { goalActivity } from '@/lib/goalActivity'
import { goalInputText, goalInputFields } from '@/lib/goalInput'
import { Button } from '@/components/ui/primitives'
import { GoalEvidence } from './GoalEvidence'

type Goal = NonNullable<GoalState['goal']>
type Receipt = Pick<Goal, 'id' | 'revision'>
type Draft = { objective: string } & { rounds: string; budget: string; base: Receipt | null }
const draftFor = (goal?: Goal | null): Draft => ({
  objective: goalInputText(goal), rounds: goal?.maxGoalRounds != null ? String(goal.maxGoalRounds) : '',
  budget: goal?.tokenBudget != null ? String(goal.tokenBudget) : '',
  base: goal ? { id: goal.id, revision: goal.revision } : null,
})
const integer = (value: string, max: number) => !!value.trim() && Number.isInteger(Number(value)) && Number(value) >= 1 && Number(value) <= max

type GoalDockProps = { sessionId: string; state: GoalState; running: boolean; openRequest: number }
export function GoalDock(props: GoalDockProps) {
  useLocale()

  // Keep drafts and outstanding requests scoped to their original chat.
  return <GoalPanel key={props.sessionId} {...props} />
}
function GoalPanel({ sessionId, state, running, openRequest }: GoalDockProps) {
  useLocale()

  const chat = useChat(sessionId)
  const [open, setOpen] = useState(() => !!useDraftStore.getState().bySession[sessionId]?.goalCreation)
  const [draft, updateDraft] = useState<Draft | null>(() => {
    const saved = useDraftStore.getState().bySession[sessionId]?.goalCreation
    return saved ? {...saved, base: null} : null
  })
  const draftRef = useRef(draft)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const setDraft = (update: Draft | null | ((current: Draft | null) => Draft | null)) => {
    // 同步保存输入，刷新或切换会话前不依赖 effect 才落盘。
    const next = typeof update === 'function' ? update(draftRef.current) : update
    useDraftStore.getState().setGoalCreation(sessionId, next && !next.base
      ? {objective: next.objective, rounds: next.rounds, budget: next.budget} : undefined)
    draftRef.current = next
    updateDraft(next)
  }
  const [deleting, setDeleting] = useState<Receipt | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const lock = useRef(false)
  const objectiveInput = useRef<HTMLTextAreaElement>(null)
  const goal = state.goal
  const selectedMode = useDraftStore(s => s.bySession[sessionId]?.executionMode)
  const [elapsed, setElapsed] = useState(goal?.elapsedSeconds ?? 0)
  useEffect(() => {
    const initial = goal?.elapsedSeconds ?? 0
    const start = performance.now()
    const update = () => setElapsed(initial + (goal?.timingActive ? (performance.now() - start) / 1000 : 0))
    update()
    if (!goal?.timingActive) return
    let timer: ReturnType<typeof setInterval> | undefined
    const visible = () => { clearInterval(timer); if (!document.hidden) { update(); timer = setInterval(update, 1000) } }
    visible()
    document.addEventListener('visibilitychange', visible)
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', visible) }
  }, [goal?.id, goal?.elapsedSeconds, goal?.timingActive, state.stateVersion])
  const duration = [Math.floor(elapsed / 3600), Math.floor(elapsed / 60) % 60, Math.floor(elapsed) % 60].map(value => String(value).padStart(2, '0')).join(':')
  const handledOpen = useRef(0)
  const previousGoalId = useRef(goal?.id)
  useEffect(() => {
    if ((!goal || goal.phase === 'complete') && selectedMode !== 'goal') { setOpen(false); setDraft(null) }
  }, [goal, selectedMode])
  useEffect(() => {
    if (previousGoalId.current !== goal?.id) setOpen(false)
    previousGoalId.current = goal?.id
  }, [goal?.id])
  useEffect(() => {
    if (!openRequest || handledOpen.current === openRequest) return
    handledOpen.current = openRequest
    setOpen(true)
    if (!goal || goal.phase === 'complete') {
      setDraft(current => current ?? draftFor())
      objectiveInput.current?.focus()
    }
  }, [openRequest, goal])
  const armed = goal?.phase === 'active' && state.activation === 'armed'
  const complete = goal?.phase === 'complete'
  const exhausted = !!goal && (goal.maxGoalRounds != null && goal.roundsStarted >= goal.maxGoalRounds || goal.tokenBudget != null && (goal.tokensUsed ?? 0) + (goal.tokensReserved ?? 0) >= goal.tokenBudget)
  const stale = !!draft && (draft.base ? draft.base.id !== goal?.id || draft.base.revision !== goal?.revision : !!goal && goal.phase !== 'complete')
  const deleteStale = !!deleting && (deleting.id !== goal?.id || deleting.revision !== goal?.revision)
  const valid = !!draft && !!draft.objective.trim() && (!draft.rounds.trim() || integer(draft.rounds, 1_000_000)) &&
    (!draft.budget.trim() || integer(draft.budget, 1_000_000_000))
  const { label, note } = goalActivity(state, { running: chat.queueRunning, stopping: chat.stopping, connected: chat.connection === 'open',
    queuePaused: chat.queuePaused, queued: chat.queued, planMode: chat.planMode,
    waiting: chat.questions.length ? chat.questions.some(question => question.questions.some(item => item.id === 'permission')) ? 'approval' : 'answer' : null })

  const control = async (action: 'create' | 'edit' | 'clear' | 'pause' | 'resume', receipt?: Receipt | null) => {
    if (lock.current) return
    lock.current = true; setBusy(true); setError('')
    const submittedDraft = draftRef.current
    const submittedCreation = useDraftStore.getState().bySession[sessionId]?.goalCreation
    try {
      const ref = receipt ?? goal
      await controlGoal(sessionId, {
        action, ...(action !== 'create' && ref ? { goal_id: ref.id, revision: ref.revision } : {}),
        ...(submittedDraft && (action === 'create' || action === 'edit') ? {
          ...goalInputFields(submittedDraft.objective, goal), max_goal_rounds: submittedDraft.rounds.trim() ? Number(submittedDraft.rounds) : null,
          token_budget: submittedDraft.budget.trim() ? Number(submittedDraft.budget) : null,
        } : {}),
      })
      const creationUnchanged = useDraftStore.getState().bySession[sessionId]?.goalCreation === submittedCreation
      if (action === 'create' && creationUnchanged) {
        // 已接受的创建即使卸载也结算原草稿，不能清除随后替换的内容或模式。
        useDraftStore.getState().setGoalCreation(sessionId)
        useDraftStore.getState().consumeExecutionMode(sessionId, 'goal')
      }
      if (mounted.current && creationUnchanged && draftRef.current === submittedDraft &&
          (action === 'create' || action === 'edit' || action === 'clear')) {
        draftRef.current = null
        updateDraft(null); setDeleting(null)
        if (action === 'create') setOpen(false)
      }
    } catch (err) {
      if (mounted.current && draftRef.current === submittedDraft) {
        setError(err instanceof Error ? failureText(err) : {key: "ui.components.chat.GoalDock.825cfa17d9cd"})
      }
    } finally { lock.current = false; if (mounted.current) setBusy(false) }
  }

  const edit = () => { setDraft(draftFor(goal)); setDeleting(null); setError('') }
  const cancel = () => {
    setDraft(null); setDeleting(null); setError('')
    if (!draft?.base) useDraftStore.getState().consumeExecutionMode(sessionId, 'goal')
    if (!goal || complete) setOpen(false)
  }
  const requestDelete = () => {
    if (!goal) { cancel(); return }
    setOpen(true); setDraft(null); setDeleting({ id: goal.id, revision: goal.revision }); setError('')
  }
  const toggle = () => {
    if (!open && !goal && !draft) setDraft(draftFor())
    setOpen(!open)
  }

  if (!goal && !draft && !error) return null

  return <section className="mb-2 w-full min-w-0 shrink-0" aria-label={t("ui.components.chat.GoalDock.34089409c6c1")}>
    <div className="overflow-hidden rounded-xl border border-line bg-surface">
      <div className="flex min-h-10 flex-wrap items-center gap-2 px-3 text-xs">
        <button type="button" className="flex w-full min-w-0 items-center gap-2 py-2 text-left focus-visible:outline-brand sm:w-auto sm:flex-1" aria-expanded={open} onClick={toggle}>
          <Target className="h-3.5 w-3.5 shrink-0 text-brand" aria-hidden="true" />
          <span className="shrink-0 font-medium text-ink">{t("ui.components.chat.GoalDock.34089409c6c1")}</span>
          <span className="min-w-0 flex-1 truncate text-muted" title={goal?.objective}>{goal?.objective || t("ui.components.chat.GoalDock.0998f815e7bd")}</span>
        </button>
        {goal && <span className="shrink-0 text-muted" role="status">{label} · {goal.roundsStarted}/{goal.maxGoalRounds ?? t('ui.goal.unlimited')} · <span className="tabular-nums" title={t('ui.goal.elapsedHint')}>{t('ui.goal.elapsed')} {duration}</span></span>}
        {goal && !complete && <button type="button" disabled={busy || exhausted && !armed} className="rounded-md p-1.5 text-muted hover:bg-raised disabled:opacity-50"
          aria-label={armed ? t("ui.components.chat.GoalDock.34a8a93ff639") : t("ui.components.chat.GoalDock.caecb5d342cc")} title={armed ? t("ui.components.chat.GoalDock.f4d142cc98e4") : exhausted ? t("ui.components.chat.GoalDock.bd0db3e8b186") : t("ui.components.chat.GoalDock.0bcadebba5cc")} onClick={() => void control(armed ? 'pause' : 'resume')}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : armed ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
        </button>}
        {!running && !chat.queueRunning && !chat.stopping && <button type="button" disabled={busy}
          className="rounded-md p-1.5 text-muted hover:bg-raised disabled:opacity-50" aria-label={t("ui.components.chat.GoalDock.b86c8d73cc04")}
          title={goal ? t("ui.components.chat.GoalDock.9c55684f70d5") : t("ui.components.chat.GoalDock.ddd2da28338e")} onClick={requestDelete}>
          <X className="h-3.5 w-3.5" />
        </button>}
        <button type="button" className="ml-auto shrink-0 rounded-md py-2 text-muted focus-visible:outline-brand"
          aria-label={t("ui.components.chat.GoalDock.34089409c6c1")} aria-expanded={open} onClick={toggle}>
          <ChevronRight className={'h-3.5 w-3.5 shrink-0 transition-transform ' + (open ? 'rotate-90' : '')} aria-hidden="true" />
        </button>
      </div>
      {open && <div className="max-h-goal space-y-3 overflow-y-auto overscroll-contain border-t border-line p-3">
        {draft ? <form className="space-y-3" aria-label={draft.base ? t("ui.components.chat.GoalDock.cd6c6f405d14") : t("ui.components.chat.GoalDock.1d6e17611a7e")} onSubmit={event => { event.preventDefault(); if (valid && !stale && !busy) void control(draft.base ? 'edit' : 'create', draft.base) }}>
          <fieldset disabled={busy} className="space-y-3">
            <label className="block text-xs text-muted">{t('ui.goal.objectiveLabel')}<textarea ref={objectiveInput} value={draft.objective} onChange={e => setDraft({ ...draft, objective: e.target.value })} rows={3} autoFocus required
                placeholder={t("ui.components.chat.GoalDock.c8666ec92fb6")}
                className="mt-1.5 block w-full resize-y rounded-lg border border-line bg-base p-2.5 text-sm text-ink focus:outline-brand" />
            </label>
            <p className="text-xs leading-5 text-muted">{t('ui.goal.unifiedHint')}</p>
            <details className="rounded-lg border border-line px-3 py-2">
              <summary className="cursor-pointer text-xs text-muted">{t('ui.goal.optionalLimits')}</summary>
              <div className="mt-3 space-y-3">
                <div role="group" aria-label={t('ui.goal.runScale')} className="flex flex-wrap gap-2">
                  {[null, 50, 500, 5000].map(rounds => <button key={rounds ?? 'unlimited'} type="button" aria-pressed={rounds == null ? !draft.rounds : Number(draft.rounds) === rounds}
                    className={'rounded-lg border px-3 py-2 text-xs transition-colors focus-visible:outline-brand ' +
                      ((rounds == null ? !draft.rounds : Number(draft.rounds) === rounds) ? 'border-brand bg-brand/10 text-ink' : 'border-line text-muted hover:bg-raised')}
                    onClick={() => setDraft({ ...draft, rounds: rounds == null ? '' : String(rounds) })}>
                    {rounds == null ? t('ui.goal.untilComplete') : t(`ui.goal.scale${rounds}`)}
                  </button>)}
                </div>
                <div className="flex flex-wrap gap-3">
                  <label className="text-xs text-muted">{t("ui.components.chat.GoalDock.6161e366fcd2")}<input type="number" min="1" max="1000000" step="1" value={draft.rounds} placeholder={t('ui.goal.untilComplete')} onChange={e => setDraft({ ...draft, rounds: e.target.value })} className="ml-2 w-28 rounded-md border border-line bg-base px-2 py-1.5 text-ink" />
                  </label>
                  <label className="text-xs text-muted">{t("ui.components.chat.GoalDock.48732944ad87")}<input type="number" min="1" max="1000000000" step="1" value={draft.budget} onChange={e => setDraft({ ...draft, budget: e.target.value })} placeholder={t("ui.components.chat.GoalDock.b94cae13412a")} className="ml-2 w-28 rounded-md border border-line bg-base px-2 py-1.5 text-ink" />
                  </label>
                </div>
              </div>
            </details>
          </fieldset>
          {draft.base && <p className="text-xs leading-5 text-muted">{t(running ? 'ui.goal.editExplanationRunning' : 'ui.goal.editExplanation')}</p>}
          {stale && <p role="alert" className="text-xs leading-5 text-warn">{t("ui.components.chat.GoalDock.60f23f7e21d2")}<button type="button" disabled={busy} className="ml-2 underline" onClick={edit}>{t("ui.components.chat.GoalDock.6acc0c28a54e")}</button>
          </p>}
          {!valid && !!draft.objective.trim() && <p className="text-xs text-warn">{t('ui.goal.invalidDraft')}</p>}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary" disabled={!valid || stale} loading={busy}>{draft.base ? t("ui.components.chat.GoalDock.6fc46974c237") : t("ui.components.chat.GoalDock.781565e70304")}</Button>
            <Button variant="secondary" disabled={busy} onClick={cancel}>{t("ui.components.chat.GoalDock.2cd0f3be8738")}</Button>
          </div>
        </form> : <>
          {goal ? <>
            <p className="whitespace-pre-wrap break-words text-sm text-ink">{goal.objective}</p>
            <GoalEvidence key={goal.id + ':' + goal.revision} sessionId={sessionId} goal={goal} />
            <p className="text-xs tabular-nums text-muted" aria-label={t("ui.components.chat.GoalDock.03f8d84521d7")}>{t(goal.tokenBudget != null
              ? goal.tokensReserved ? 'ui.goal.usageBudgetReserved' : 'ui.goal.usageBudget'
              : goal.tokensReserved ? 'ui.goal.usageUnlimitedReserved' : 'ui.goal.usageUnlimited', {
              used: (goal.tokensUsed ?? 0).toLocaleString(getLocale()),
              budget: goal.tokenBudget?.toLocaleString(getLocale()) ?? '',
              reserved: (goal.tokensReserved ?? 0).toLocaleString(getLocale()),
            })}
            </p>
            {!!goal.tokensUncertain && <p role="status" className="text-xs leading-5 text-warn">{t('ui.goal.usageUncertain', { count: goal.tokensUncertain.toLocaleString(getLocale()) })}</p>}
            <p className="text-xs leading-5 text-muted">{note}</p>
            {goal.retryAt != null && goal.retryError && <p role="status" className="break-words text-xs leading-5 text-warn">{t('ui.goal.retryError', { error: goal.retryError })}</p>}
            {deleting ? <div className="space-y-2 rounded-lg border border-danger/25 p-3" role="group" aria-label={t("ui.components.chat.GoalDock.81b1320658cc")}>
              <p className="text-xs leading-5 text-ink">{t("ui.components.chat.GoalDock.dfbf1fc7a5b4")}</p>
              {deleteStale && <p role="alert" className="text-xs text-warn">{t("ui.components.chat.GoalDock.00f6ea64cd94")}</p>}
              <div className="flex gap-2">
                <Button variant="danger" disabled={deleteStale} loading={busy} onClick={() => void control('clear', deleting)}>{t("ui.components.chat.GoalDock.81b1320658cc")}</Button>
                <Button variant="secondary" disabled={busy} onClick={cancel}>{t("ui.components.chat.GoalDock.2cd0f3be8738")}</Button>
              </div>
            </div> : <div className="flex flex-wrap gap-2">
              {!complete && <Button variant="secondary" disabled={busy} icon={<Pencil className="h-3.5 w-3.5" />} onClick={edit}>{t("ui.components.chat.GoalDock.127636b82d8b")}</Button>}
              <Button variant="secondary" disabled={busy} icon={<Trash2 className="h-3.5 w-3.5" />} onClick={requestDelete}>{t("ui.components.chat.GoalDock.b528ac824f74")}</Button>
            </div>}
          </> : <Button size="sm" variant="primary" onClick={edit}>{t("ui.components.chat.GoalDock.8043a889b7e6")}</Button>}
        </>}
      </div>}
      {error && <p role="alert" className="border-t border-line px-3 py-2 text-xs text-danger">{noticeText(error)}</p>}
    </div>
  </section>
}
