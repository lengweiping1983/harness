import { noticeText } from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { memo, useEffect, useId, useRef, useState } from 'react'
import { Check, HelpCircle, Send } from 'lucide-react'
import type { QuestionBlock as QuestionBlockModel } from '@/types'
import type { QuestionAnswer } from '@/lib/protocol.generated'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/primitives'
import { Markdown } from '@/components/markdown/Markdown'
import { readPlanFeedback, savePlanFeedback, clearPlanFeedback, planFeedbackUnpersisted, planFeedbackProtected } from '@/lib/planFeedbackDraft'
import type { QuestionSubmissionFeedback } from '@/lib/questionSubmission'
import { allowsCustomAnswer, questionReady } from '@/lib/questionAnswers'
import { readQuestionDraft, saveQuestionDraft, clearQuestionDraft, draftableQuestions, questionDraftProtected } from '@/lib/questionDraft'
import {PermissionOperationDetails} from './PermissionOperationDetails'

/**
 * 交互式提问卡片。
 *
 * 模型的 `ask_user_question` 会挂起整个回合，所以这块必须显眼、可键盘操作、
 * 并且**未作答时永远钉在视口内**——用户往下翻历史很容易找不到它。
 */

export interface QuestionBlockProps {
  block: QuestionBlockModel
  onAnswer: (questionId: string, answers: unknown) => void
  busy?: boolean
  readOnly?: boolean
  sessionId: string | null
  submissionFeedback?: QuestionSubmissionFeedback
}

function ownAnswer<T>(values: Record<string, T>, key: string, fallback: T): T {
  return Object.hasOwn(values, key) ? values[key] : fallback
}

export const QuestionCard = memo(function QuestionCard({ block, onAnswer, busy, sessionId, submissionFeedback, readOnly: viewOnly = false }: QuestionBlockProps) {
  useLocale()

  const savedAnswers = Array.isArray(block.answers) ? block.answers as QuestionAnswer[] : []
  const formId = useId()
  const questionFields = useRef<(HTMLFieldSetElement | null)[]>([])
  const questionTitles = block.questions.map((question, index, questions) => {
    if (question.id === 'permission' && question.operation?.approval) return t('ui.permissions.approvalTitle')
    const title = question.header?.trim()
    return title ? questions.filter((item) => item.header?.trim() === title).length > 1
      ? t("ui.components.chat.blocks.QuestionCard.67266abd6632", {p0: (title), p1: (index + 1)}) : title : t("ui.components.chat.blocks.QuestionCard.172a035d37a8", {p0: (index + 1)})
  })
  const planQuestion = block.questions.find((question) => question.id === 'plan-review')
  const [restoredFeedback] = useState(() => !viewOnly && planQuestion ? readPlanFeedback(sessionId, block.questionId) : '')
  const [restoredDraft] = useState(() => !viewOnly ? readQuestionDraft(sessionId, block.questionId, block.questions) : { selected: {}, text: {} })
  const [draft, setDraft] = useState<Record<string, string[]>>(restoredDraft.selected)
  const [text, setText] = useState<Record<string, string>>((): Record<string, string> => restoredFeedback ? { 'plan-review': restoredFeedback } : restoredDraft.text)
  const [planFeedbackOpen, setPlanFeedbackOpen] = useState(() => Boolean(restoredFeedback || restoredDraft.text['plan-review']
    || savedAnswers.some(answer => answer.id === 'plan-review' && answer.custom)))
  const [questionDraftFailed, setQuestionDraftFailed] = useState(false)
  const [draftSaveFailed, setDraftSaveFailed] = useState(() => Boolean(!viewOnly && planQuestion && !block.answered && !block.closed && planFeedbackUnpersisted(sessionId, block.questionId)))
  const answeredRef = Boolean(block.answered)
  const draftProtected = questionDraftProtected(sessionId, block.questionId) || planFeedbackProtected(sessionId, block.questionId)
  const readOnly = viewOnly || answeredRef || Boolean(block.closed)
  useEffect(() => {
    if (viewOnly) return
    if (readOnly) clearQuestionDraft(sessionId, block.questionId)
    else if (draftableQuestions(block.questions)) setQuestionDraftFailed(!saveQuestionDraft(sessionId, block.questionId, block.questions, { selected: draft, text }))
  }, [sessionId, block.questionId, block.questions, readOnly, viewOnly, draft, text])
  useEffect(() => {
    if (!viewOnly && planQuestion && readOnly) clearPlanFeedback(sessionId, block.questionId)
  }, [sessionId, block.questionId, Boolean(planQuestion), readOnly, viewOnly])
  const planPicked = planQuestion ? ownAnswer(draft, 'plan-review', [])[0] : undefined
  const approveOption = planQuestion?.options?.find(option => option.id === 'approve')
  const keepOption = planQuestion?.options?.find(option => option.id === 'keep-planning')
  const directPlanActions = block.questions.length === 1 && planQuestion && !planQuestion.multi_select
    && planQuestion.options?.length === 2
    && Boolean(approveOption?.label && keepOption?.label)
  const planFeedback = ownAnswer(text, 'plan-review', '').trim()
  const permissionQuestion = block.questions.find((question) => question.id === 'permission')
  const permissionKeys = ['ui.components.chat.blocks.QuestionCard.f9881e36e9cd', 'ui.components.chat.blocks.QuestionCard.4e9b6b4d7c21', 'ui.components.chat.blocks.QuestionCard.136de7a8c46f']
  const permissionLabels = permissionKeys.map(key => t(key))
  const permissionIds = ['allow-once', 'allow-temporarily', 'deny']
  const permissionOptions = permissionIds.map(id => permissionQuestion?.options?.find(option => option.id === id))
  const directPermissionActions = block.questions.length === 1 && permissionQuestion && !permissionQuestion.multi_select
    && permissionQuestion.options?.length === permissionLabels.length
    && permissionOptions.every(option => Boolean(option?.label))

  const submit = () => {
    if (viewOnly) return
    const answers: QuestionAnswer[] = block.questions.map((question, index) => {
      const key = question.id ?? String(index)
      if (question.options?.length) {
        const picked = ownAnswer(draft, key, [])
        return { id: key, selected: question.multi_select ? picked : picked.slice(0, 1),
          ...(ownAnswer(text, key, '').trim() ? { custom: ownAnswer(text, key, '').trim() } : {}) }
      }
      return { id: key, selected: [], custom: ownAnswer(text, key, '') }
    })
    onAnswer(block.questionId, answers)
  }

  const missing = block.questions.flatMap((question, index) => {
    const key = question.id ?? String(index)
    return questionReady(question, ownAnswer(draft, key, []), ownAnswer(text, key, '')) ? [] : [index]
  })
  const ready = missing.length === 0
  const ordinaryForm = block.questions.every(allowsCustomAnswer)

  return (
    <section
      id={'q-' + block.questionId}
      className={cn(
        'flex min-h-0 flex-col overflow-hidden rounded-xl border shadow-glow',
        answeredRef ? 'border-ok/30 bg-ok/[0.04]' : 'border-brand/40 bg-brand/[0.06]',
      )}
    >
      <header
        className={cn(
          'flex shrink-0 items-center gap-2 border-b px-3 py-2',
          answeredRef ? 'border-ok/20' : 'border-brand/25',
        )}
      >
        {answeredRef ? (
          <Check className="h-3.5 w-3.5 text-ok" />
        ) : (
          <HelpCircle className="h-3.5 w-3.5 text-brand animate-pulse" />
        )}
        <span className="text-xs font-medium text-ink">
          {block.closed ? t("ui.components.chat.blocks.QuestionCard.eff53696e2b4") : answeredRef ? permissionQuestion ? t("ui.components.chat.blocks.QuestionCard.606cc5889fe2") : t("ui.components.chat.blocks.QuestionCard.474c9878e5db") : viewOnly ? t('ui.shared.pendingQuestion') : planQuestion ? t("ui.components.chat.blocks.QuestionCard.da4727d1f404") : permissionQuestion ? t("ui.components.chat.blocks.QuestionCard.85bbb56b27d9") : t("ui.components.chat.blocks.QuestionCard.69f946a71aec")}
        </span>
      </header>

      <div className="min-h-0 space-y-3 overflow-y-auto px-3 py-3">
        {!viewOnly && draftProtected ? <p role="alert" className="text-xs text-warn">{t('ui.question.invalidDraft')}</p> : null}
        {!readOnly && ordinaryForm && block.questions.length > 1 ? <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span role="status" aria-atomic="true">{t('ui.question.progress', {completed: block.questions.length - missing.length, total: block.questions.length})}</span>
          {missing.length ? <button type="button" disabled={busy} className="rounded px-1 py-1 text-brand focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand"
            onClick={() => questionFields.current[missing[0]]?.querySelector<HTMLButtonElement | HTMLTextAreaElement>('button:not(:disabled), textarea:not(:disabled)')?.focus()}>{t('ui.question.goToUnanswered', {title: questionTitles[missing[0]]})}
          </button> : null}
        </div> : null}
        {!readOnly && questionDraftFailed ? <p role="status" className="text-xs text-danger">{t("ui.components.chat.blocks.QuestionCard.2ccd3181b66f")}</p> : !readOnly && (Object.values(restoredDraft.text).some(Boolean) || Object.values(restoredDraft.selected).some((v) => v.length)) ? <p role="status" className="text-xs text-muted">{t("ui.components.chat.blocks.QuestionCard.35016df92b46")}</p> : null}
        {block.questions.map((question, index) => {
          const key = question.id ?? String(index)
          const saved = savedAnswers.find((answer) => answer.id === key)
          const picked = answeredRef ? saved?.selected ?? [] : ownAnswer(draft, key, [])
          const toggle = (label: string) => {
            setDraft((prev) => {
              const current = ownAnswer(prev, key, [])
              if (question.multi_select) {
                return {
                  ...prev,
                  [key]: current.includes(label)
                    ? current.filter((v) => v !== label)
                    : [...current, label],
                }
              }
              return { ...prev, [key]: current[0] === label ? [] : [label] }
            })
          }
          return (
            <fieldset key={key} ref={(element) => { questionFields.current[index] = element }} aria-describedby={allowsCustomAnswer(question) ? `${formId}-question-${index}` : undefined} className="space-y-1.5 min-w-0">
              {question.header || ordinaryForm ? (
                <legend className="text-2xs font-semibold text-brand">
                  {questionTitles[index]}
                </legend>
              ) : null}
              {question.id === 'plan-review' ? <>
                <p className="text-xs text-muted">{t("ui.components.chat.blocks.QuestionCard.38bfc1e1ba22")}</p>
                <div aria-label={t("ui.components.chat.blocks.QuestionCard.1349eecb51e9")} tabIndex={0} className="max-h-question overflow-y-auto rounded-lg border border-line bg-surface p-3">
                  <Markdown dense>{question.question}</Markdown>
                </div>
              </> : question.id === 'permission' ? <>{directPermissionActions ? <p className="text-xs leading-relaxed text-muted">{t(question.operation?.approval ? 'ui.permissions.approvalHint' : 'ui.components.chat.blocks.QuestionCard.f6628671cbdb')}</p> : null}
              {question.operation ? <PermissionOperationDetails operation={question.operation} /> : null}
              <details open><summary className="cursor-pointer text-xs text-muted">{t('ui.permissions.details')}</summary><div aria-label={t("ui.components.chat.blocks.QuestionCard.f6ea537aab6b")} tabIndex={0}
                className="max-h-question-mobile overflow-y-auto rounded-lg border border-line bg-surface p-3 sm:max-h-96">
                <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-ink [overflow-wrap:anywhere]">{question.question}</pre>
              </div></details></> : <p id={`${formId}-question-${index}`} className="whitespace-pre-wrap break-words text-compact font-medium leading-relaxed text-ink">{question.question}</p>}

              {(directPlanActions || directPermissionActions) && !readOnly ? null : question.options?.length ? (
                <div className="flex flex-wrap gap-1.5 pt-0.5">
                  {question.options.map((option) => {
                    const label = option.label ?? String(option.id ?? '')
                    const active = picked.includes(label)
                    return (
                      <button
                        key={label}
                        type="button"
                        disabled={readOnly || busy}
                        aria-pressed={active}
                        onClick={() => toggle(label)}
                        title={option.description}
                        className={cn(
                          'flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition-all duration-150 ease-swift',
                          active
                            ? 'border-brand/50 bg-brand/[0.12] text-brand'
                            : 'border-line bg-surface text-muted hover:border-line-strong hover:text-ink',
                          answeredRef && 'cursor-default opacity-70',
                        )}
                      >
                        {active ? <Check className="h-3 w-3" /> : null}
                        {question.id === 'permission' ? permissionLabels[permissionIds.indexOf(option.id ?? '')] ?? label : question.id === 'plan-review' ? (option.id === 'approve' ? t("ui.components.chat.blocks.QuestionCard.b168fdafe0f4") : option.id === 'keep-planning' ? t("ui.components.chat.blocks.QuestionCard.b367cdfb55b5") : label) : label}
                      </button>
                    )
                  })}
                </div>
              ) : viewOnly ? null : (
                <div className="space-y-2">
                <textarea
                  aria-label={t("ui.components.chat.blocks.QuestionCard.b90550dabb9b", {p0: (questionTitles[index])})}
                  aria-describedby={allowsCustomAnswer(question) ? `${formId}-question-${index}` : undefined}
                  value={answeredRef ? saved?.custom ?? '' : ownAnswer(text, key, '')}
                  disabled={readOnly || busy}
                  onChange={(event) => setText((prev) => ({ ...prev, [key]: event.target.value }))}
                  rows={3}
                  placeholder={t("ui.components.chat.blocks.QuestionCard.96c5bdd46d76")}
                  className={cn(
                    'w-full resize-y rounded-lg border border-line bg-surface px-2.5 py-2',
                    'text-compact text-ink placeholder:text-faint focus:border-brand/50 focus:outline-none',
                  )}
                />
                </div>
              )}
              {!viewOnly && question.options?.length && allowsCustomAnswer(question) ? <details open={Boolean(answeredRef ? saved?.custom : ownAnswer(text, key, ''))}><summary className="cursor-pointer pt-2 text-xs text-muted">{t('ui.question.writeAnswer')}</summary><label className="block space-y-1 pt-2 text-xs text-muted">
                <span>{t("ui.components.chat.blocks.QuestionCard.78869c636907")}</span>
                <textarea aria-label={t("ui.components.chat.blocks.QuestionCard.3d7162976d6c", {p0: (questionTitles[index])})} aria-describedby={`${formId}-question-${index}`} rows={2}
                  disabled={readOnly || busy} value={answeredRef ? saved?.custom ?? '' : ownAnswer(text, key, '')}
                  onChange={(event) => setText((previous) => ({ ...previous, [key]: event.target.value }))}
                  placeholder={t("ui.components.chat.blocks.QuestionCard.2784025e7338")}
                  className="w-full resize-y rounded-lg border border-line bg-surface px-2.5 py-2 text-compact text-ink focus:border-brand/50 focus:outline-none" />
              </label></details> : null}
              {!viewOnly && question.id === 'plan-review' ? <details open={planFeedbackOpen} onToggle={event => setPlanFeedbackOpen(event.currentTarget.open)}>
                <summary className="cursor-pointer pt-2 text-xs text-muted">{t("ui.components.chat.blocks.QuestionCard.6a529601daca")}</summary>
                <label className="block space-y-1 pt-2 text-xs text-muted">
                <textarea aria-label={t("ui.components.chat.blocks.QuestionCard.6a612d9e2134")} rows={3} disabled={readOnly || busy}
                  value={answeredRef ? saved?.custom ?? '' : ownAnswer(text, key, '')}
                  onChange={(event) => {
                    const value = event.target.value
                    setText((prev) => ({ ...prev, [key]: value }))
                    setDraftSaveFailed(!savePlanFeedback(sessionId, block.questionId, value))
                    if (value.trim()) setDraft((prev) => ({ ...prev, [key]: keepOption?.label ? [keepOption.label] : [] }))
                  }} placeholder={t("ui.components.chat.blocks.QuestionCard.1ba0160dbe9f")}
                  className="w-full resize-y rounded-lg border border-line bg-surface px-2.5 py-2 text-compact text-ink focus:border-brand/50 focus:outline-none" />
                {!readOnly && draftSaveFailed ? <span role="status" className="block text-danger">{t("ui.components.chat.blocks.QuestionCard.cca7950b6764")}</span> : !readOnly && restoredFeedback && ownAnswer(text, key, '') === restoredFeedback ? <span role="status" className="block">{t("ui.components.chat.blocks.QuestionCard.e6fc18f351bf")}</span> : null}
              </label></details> : null}
              {viewOnly && <p className="pt-2 text-xs text-muted whitespace-pre-wrap">{answeredRef ? saved?.custom ?? '' : t('ui.shared.waitingOwner')}</p>}
            </fieldset>
          )
        })}

      </div>
      {!readOnly ? <div className="shrink-0 space-y-3 border-t border-line px-3 py-3">
        {(
          directPermissionActions ? <div className="flex flex-wrap items-center gap-2 pt-0.5">
            {permissionLabels.map((label, index) => <Button key={index} size="sm" disabled={busy}
              variant={index === 2 ? 'danger' : index === 0 ? 'primary' : 'secondary'}
              onClick={() => onAnswer(block.questionId, [{ id: 'permission', selected: [permissionOptions[index]!.label ?? ''] }])}>
              {label}
            </Button>)}
            {busy ? <p role="status" className="w-full text-2xs text-muted">{t("ui.components.chat.blocks.QuestionCard.87b04f3dc6ca")}</p> : null}
          </div> : directPlanActions ? <div className="flex flex-wrap items-center gap-2 pt-0.5">
            <Button variant="primary" size="sm" disabled={busy || Boolean(planFeedback)}
              loading={busy} icon={<Check className="h-3.5 w-3.5" />}
              onClick={() => onAnswer(block.questionId, [{ id: 'plan-review', selected: [approveOption!.label!], selected_ids: ['approve'] }])}>{t("ui.components.chat.blocks.QuestionCard.b168fdafe0f4")}</Button>
            <Button size="sm" disabled={busy} onClick={() => onAnswer(block.questionId, [{
              id: 'plan-review', selected: [keepOption!.label!], selected_ids: ['keep-planning'], ...(planFeedback ? { custom: planFeedback } : {}),
            }])}>
              {planFeedback ? t("ui.components.chat.blocks.QuestionCard.4c381122f9a2") : t("ui.components.chat.blocks.QuestionCard.b367cdfb55b5")}
            </Button>
            {planFeedback ? <p className="w-full text-2xs text-muted">{t("ui.components.chat.blocks.QuestionCard.336935b2e022")}</p> : null}
          </div> : <div className="flex items-center gap-2 pt-0.5">
            <Button
              variant="primary"
              size="sm"
              disabled={!ready || busy}
              loading={busy}
              icon={<Send className="h-3.5 w-3.5" />}
              onClick={submit}
            >
              {planQuestion ? Boolean(approveOption?.label && planPicked === approveOption.label) ? t("ui.components.chat.blocks.QuestionCard.b168fdafe0f4") : t("ui.components.chat.blocks.QuestionCard.4c381122f9a2") : t("ui.components.chat.blocks.QuestionCard.0c1762dddc4f")}
            </Button>
            {!ready ? (
              <span className="text-2xs text-faint">{t("ui.components.chat.blocks.QuestionCard.d766cf1f62f6")}</span>
            ) : null}
          </div>
        )}
        {!readOnly && submissionFeedback ? <div role="alert" aria-label={t("ui.components.chat.blocks.QuestionCard.14e01c16c5cb")} className="space-y-1 rounded-lg border border-danger/30 bg-danger/5 p-2 text-xs leading-relaxed [overflow-wrap:anywhere]">
          <p className="font-medium text-danger">{noticeText(submissionFeedback.title)}</p>
          <p className="max-h-24 overflow-auto whitespace-pre-wrap text-muted" tabIndex={0}>{noticeText(submissionFeedback.message)}</p>
          <p className="text-muted">{submissionFeedback.unconfirmed ? t("ui.components.chat.blocks.QuestionCard.7be2b752f508") : t("ui.components.chat.blocks.QuestionCard.da7000e1172f")}</p>
        </div> : null}
      </div> : null}
    </section>
  )
})
