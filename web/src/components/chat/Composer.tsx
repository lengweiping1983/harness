import {noticeText} from '@/lib/noticeText'
import {ComposerDock, ComposerCard, ComposerEditor, ComposerToolbar, ComposerSendButton} from './ComposerSurface'
import {NewConversationProject} from './NewConversationProject'
import {ConversationSuggestions} from './ConversationSuggestions'
import { ComposerAttachments } from './ComposerAttachments'
import { useComposerSubmission } from '@/hooks/useComposerSubmission'
import { useComposerKeyboard } from '@/hooks/useComposerKeyboard'
import { useComposerCompletion } from '@/hooks/useComposerCompletion'
import { useComposerEditor } from '@/hooks/useComposerEditor'
import { ComposerRemoveButton } from './ComposerRemoveButton'
import { t, useLocale } from '@/i18n'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import {
  ListChecks,
  Loader2,
  Pause,
  Square,
  Target,
  WifiOff,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { caretAfterQuote, insertQuote } from '@/lib/quote'
import { detectTrigger } from '@/lib/trigger'
import { resolveComposerMode, type ComposerMode } from '@/lib/composerMode'
import { useAppStore } from '@/store/app'
import { useStopStore } from '@/store/stops'
import { usePlanModeStore } from '@/store/planModes'
import { Badge, Button, Tooltip } from '@/components/ui/primitives'
import { TriggerMenu } from './TriggerMenu'
import { ComposerAddMenu } from './ComposerAddMenu'
import { SessionMemberDock } from './MemberDock'
import { TaskDock } from './TaskDock'
import { GenerationDock } from './GenerationDock'
import { pendingReplyInput } from '@/lib/replyFeedback'
import { SteeringDock } from './SteeringDock'
import { QueuedFollowups } from './QueuedFollowups'
import type { SteeringReceipt } from '@/lib/protocol.generated'
import type { GenerationState } from '@/lib/generation'
import { ModelMenu, ModelSeat } from './ModelPicker'
import {useAnchoredMenu} from '@/hooks/useAnchoredMenu'
import { fileCatalogStatus } from '@/lib/fileReferences'
import { reasoningSelection } from '@/lib/reasoningSelection'
import { DropOverlay } from './AttachmentDraft'
import { PermissionMenu } from './PermissionMenu'
import { VoiceButton } from './VoiceButton'
import {useVoiceInputSupported} from '@/hooks/useVoiceInputSupported'
import { ScreenshotButton } from './ScreenshotButton'
import { useChatStore } from '@/store/chat'
import { queuedTeamAddress, validTeamAddress, type TeamAddress } from '@/lib/agentMount'
import { TeamRecipientPicker } from './TeamRecipientPicker'
import { useDraftStore, EMPTY_SUBMISSIONS } from '@/store/drafts'
import { useAttachmentDrafts } from '@/hooks/useAttachmentDrafts'
import type { AttachmentInfo, TodoItem } from '@/types'

/**
 * 输入区。
 *
 * 交互要点（这些细节是"能用"和"顺手"的分界）：
 *   - Enter 发送 / Shift+Enter 换行 / Esc 取消生成；
 *   - textarea 自增高，超过 8 行后内部滚动，不顶走整页布局；
 *   - plan mode 是一个显式开关而不是隐藏参数，状态常驻可见；
 *   - 断线时输入框仍可编辑（草稿不该丢），但发送禁用并说明原因；
 *   - 排队中显示队列长度，让用户知道"消息没丢"。
 */

export interface ComposerProps {
  steering?: SteeringReceipt[]
  generation?: GenerationState | null
  todos?: TodoItem[]
  running: boolean
  stopping?: boolean
  queued: number
  queuePaused?: boolean
  showSuggestions?: boolean
  planMode: boolean
  planPending?: boolean | null
  connection: string
  placeholder?: string
  onSend: (text: string, attachments?: AttachmentInfo[], teamAddress?: TeamAddress) => void | boolean | Promise<boolean>
  onCancel: () => void
  onTogglePlan: (active: boolean) => void
  goalPanel?: ReactNode
  onOpenGoal?: () => void
  onPaste?: (text: string) => void
}

export function Composer({
  steering = [],
  generation = null,
  todos = [],
  running,
  stopping = false,
  queued,
  queuePaused = false,
  showSuggestions = false,
  planMode,
  planPending = null,
  connection,
  placeholder = t("ui.components.chat.Composer.dc2f7127b291"),
  onSend,
  onCancel,
  onOpenGoal,
  goalPanel,
  onPaste,
}: ComposerProps) {
  const locale = useLocale()
  const voiceSupported = useVoiceInputSupported()

  const sessionId = useAppStore((s) => s.activeSessionId)
  const waitingInput = useChatStore(s => sessionId ? pendingReplyInput(s.sessions[sessionId]?.blocks ?? []) : null)
  const stopRequest = useStopStore((s) => sessionId ? s.requests[sessionId] : undefined)
  const stopBusy = stopping || stopRequest?.phase === 'pending' || stopRequest?.phase === 'accepted'
  const planSave = usePlanModeStore((s) => sessionId ? s.saves[sessionId] : undefined)
  const [queueWorking, setQueueWorking] = useState(false)
  const creatingSession = useAppStore(s => s.sessionCreating)
  const newSessionRevision = useAppStore(s => s.newSessionRevision)
  const baseControlBusy = creatingSession || queueWorking || stopBusy || Boolean(planSave?.pending)
  const draftKey = sessionId ?? '__unsaved__'
  const teamAddress = useDraftStore(s => s.bySession[draftKey]?.teamAddress)
  const agentMount = useChatStore(s => sessionId ? s.sessions[sessionId]?.agentMount ?? null : null)
  const storedMode = useDraftStore(s => s.bySession[draftKey]?.executionMode)
  const mode = resolveComposerMode(storedMode, planMode)
  const hasGoal = useChatStore(s => Boolean(sessionId && s.sessions[sessionId]?.goalState.goal && s.sessions[sessionId].goalState.goal?.phase !== 'complete'))
  const externalAgent = agentMount?.agent_type === 'agent' ? agentMount.runner : undefined
  const recipientValid = validTeamAddress(teamAddress, agentMount)
  const value = useDraftStore((s) => s.bySession[draftKey]?.text ?? '')
  const draftError = useDraftStore((s) => s.storageError)
  const submissions = useDraftStore((s) => s.submissions[draftKey] ?? EMPTY_SUBMISSIONS)
  const setValue = useCallback((text: string) => useDraftStore.getState().setText(draftKey, text), [draftKey])
  const { taRef, dockRef, wrapRef, pendingCompletion, valueRef, resize, scheduleFrame, captureFrames } = useComposerEditor(value, draftKey)
  const selectMode = useCallback((next: ComposerMode, focus = true) => {
    useDraftStore.getState().setExecutionMode(draftKey, next === 'plan' && planMode ? undefined : next)
    if (next !== 'plan' && planMode && sessionId) void usePlanModeStore.getState().change(sessionId, false)
    const openingGoal = next === 'goal' && !hasGoal && Boolean(onOpenGoal)
    if (openingGoal) onOpenGoal?.()
    if (focus && !openingGoal) scheduleFrame(() => taRef.current?.focus())
  }, [draftKey, planMode, sessionId, hasGoal, onOpenGoal, scheduleFrame])
  const shortcutsOpen = useAppStore((s) => s.shortcutsOpen)
  const setShortcutsOpen = useAppStore((s) => s.setShortcutsOpen)
  const pendingQuote = useAppStore((s) => s.pendingQuote)
  const clearQuote = useAppStore((s) => s.clearQuote)
  const modelMenuOpen = useAppStore((s) => s.modelMenuOpen)
  const setModelMenuOpen = useAppStore((s) => s.setModelMenuOpen)
  const models = useAppStore((s) => s.models)
  const defaultModel = useAppStore((s) => s.defaultModel)
  const sessionModel = useAppStore((s) =>
    s.activeSessionId ? (s.sessions.find((x) => x.id === s.activeSessionId)?.model ?? null) : null,
  )
  const sessionEffort = useAppStore((s) =>
    reasoningSelection(s.sessions.find((x) => x.id === s.activeSessionId), s.defaultReasoningEffort).value,
  )

  // 模型菜单只有一个渲染点（这里）。按钮和 /model 命令共用 store 里的
  // modelMenuOpen，谁打开的都由这里画，避免同一时刻挂两份菜单。
  const modelSeatRef = useRef<HTMLButtonElement | null>(null)
  const modelMenuStyle = useAnchoredMenu(modelSeatRef, modelMenuOpen, 'model')

  const lastQuoteSeq = useRef(0)

  const disconnected = connection !== 'open'

  /* ---------- 附件 ---------- */
  const attachments = useAttachmentDrafts()
  const previousAgent = useRef<{key: string; agent: string | null} | null>(null)
  useEffect(() => {
    if (!agentMount) return
    const previous = previousAgent.current
    previousAgent.current = {key: draftKey, agent: agentMount.agent}
    // 空输入框切换身份时结束上一段直达对话；有内容的草稿仍须明确确认收件人。
    if (previous?.key === draftKey && previous.agent !== agentMount.agent && teamAddress
        && !value && attachments.drafts.length === 0) {
      useDraftStore.getState().setTeamAddress(draftKey)
    }
  }, [draftKey, agentMount, teamAddress, value, attachments.drafts.length])
  const pickerRef = useRef<HTMLInputElement | null>(null)
  // 纯附件消息也允许发送：用户可能只想丢一张图过去，不打字。
  const hasContent = value.trim().length > 0 || attachments.readyRefs.length > 0
  const canSend = hasContent && !disconnected && attachments.ready && recipientValid


  const { trigger, setTrigger, triggerMenuId, closeTrigger, files, fileRoot, items, allItems, activeIndex, showMoreReferences, syncTrigger, pick, selectCandidate, handleCompletionKey } = useComposerCompletion({draftKey, sessionId, taRef, valueRef, pendingCompletion, setValue, resize, scheduleFrame, selectMode})

  useEffect(() => {
    if (!pendingQuote) return
    if (pendingQuote.sessionId !== sessionId || useAppStore.getState().activeSessionId !== sessionId) {
      clearQuote(pendingQuote.seq)
      return
    }
    // 同一条引用被重复投递（双击、快捷键连按）时只生效一次。
    if (pendingQuote.seq === lastQuoteSeq.current) return
    lastQuoteSeq.current = pendingQuote.seq

    const prev = valueRef.current
    const caret = taRef.current?.selectionStart ?? prev.length
    const next = insertQuote(prev, pendingQuote.text, caret)
    // 光标落在引用块之后的尾换行处，用户可以立刻接着写。
    const target = caretAfterQuote(prev, pendingQuote.text, caret)

    setValue(next)
    valueRef.current = next
    setTrigger(null)
    clearQuote(pendingQuote.seq)

    scheduleFrame(() => {
      const node = taRef.current
      if (!node || useAppStore.getState().activeSessionId !== pendingQuote.sessionId) return
      node.focus()
      // rAF 之后再夹一次：setValue 触发的重渲染可能还没把新值写进 DOM。
      const safe = Math.min(target, node.value.length)
      node.setSelectionRange(safe, safe)
      scheduleFrame(resize)
    })
  }, [pendingQuote, clearQuote, resize, sessionId, setValue, scheduleFrame])

  /* ---------- 切换任务只重置交互状态，保留各自草稿 ---------- */
  useEffect(() => {
    setTrigger(null)
    scheduleFrame(resize)
  }, [draftKey, resize, scheduleFrame])

  const { submit, submitting } = useComposerSubmission({value, mode, hasGoal, planMode,
    sessionId, selectMode, setValue, setTrigger, teamAddress, draftKey,
    disconnected, canSend, controlBusy: baseControlBusy, attachments, onSend, resize, captureFrames})
  const controlBusy = baseControlBusy || submitting
  const onKeyDown = useComposerKeyboard({running, shortcutsOpen, setShortcutsOpen,
    onCancel, submit, handleCompletionKey})

  // 遮罩挂到 body：它自称"全视口"，就必须真的是全视口。留在 Composer 的
  // DOM 里会被祖先的 overflow / transform 裁掉，用户看到半截提示，
  // 以为界面坏了。portal 让它只受 z-index 约束。
  const overlayHost = typeof document === 'undefined' ? null : document.body

  const hint = useMemo(() => {
    if (disconnected) return t("ui.components.chat.Composer.f1582dcf9427")
    if (planPending !== null) return null
    if (planMode) return t("ui.components.chat.Composer.77bdeab7a5b5")
    if (mode === 'plan') return t('ui.composerMode.planHint')
    return null
  }, [disconnected, mode, planMode, planPending, locale])

  return (
    <>
      {overlayHost && attachments.dragActive
        ? createPortal(
            <DropOverlay active count={attachments.dragCount} />,
            overlayHost,
          )
        : null}
      <ComposerDock ref={dockRef}>

        {planSave ? <div role={planSave.pending ? 'status' : 'alert'} aria-label={t("ui.components.chat.Composer.7d5b650bec5e")} className="mb-2 space-y-1 px-1 text-xs leading-relaxed [overflow-wrap:anywhere]">
          <p className="text-muted">{planSave.pending ? t(planSave.target ? 'ui.composerFeedback.enterPending' : 'ui.composerFeedback.exitPending') : planSave.unconfirmed ? t("ui.components.chat.Composer.7d6bc9201aee") : t("ui.components.chat.Composer.1cb62bea1ef3")}</p>
          {planSave.message ? <p tabIndex={0} className="max-h-20 overflow-auto text-muted">{noticeText(planSave.message)}</p> : null}
          {!planSave.pending && sessionId ? <div className="flex flex-wrap gap-3">
            <button type="button" className="text-brand underline" onClick={() => void usePlanModeStore.getState().change(sessionId, planSave.target)}>{t(planSave.target ? 'ui.composerFeedback.retryEnterPlan' : 'ui.composerFeedback.retryExitPlan')}</button>
            <button type="button" className="text-muted underline" onClick={() => usePlanModeStore.getState().dismiss(sessionId)}>{t("ui.components.chat.Composer.0112ed32fc4a")}</button>
          </div> : null}
          {planSave.unconfirmed ? <p className="text-muted">{t("ui.components.chat.Composer.c79b78e1d0f6")}</p> : null}
        </div> : null}
        {planPending !== null ? <p role="status" aria-label={t("ui.components.chat.Composer.3cc73574d1cd")} className="mb-2 px-1 text-xs leading-relaxed text-muted">{planPending ? t("ui.components.chat.Composer.bbfbfa519519") : t("ui.components.chat.Composer.528cd30d2ae1")}</p> : null}
        {running && stopRequest ? <div role={['rejected', 'unconfirmed'].includes(stopRequest.phase) ? 'alert' : 'status'} aria-label={t("ui.components.chat.Composer.909ab35b13c9")}
          className="mb-2 space-y-1 px-1 text-xs leading-relaxed [overflow-wrap:anywhere]">
          <p className={stopRequest.phase === 'rejected' ? 'text-danger' : 'text-muted'}>
            {stopRequest.phase === 'pending' ? t("ui.components.chat.Composer.dc628fffec0b") : stopRequest.phase === 'accepted' ? t("ui.components.chat.Composer.a54d59cb1943") : stopRequest.phase === 'rejected' ? t("ui.components.chat.Composer.f00a9f8b62f8") : t("ui.components.chat.Composer.b102a46a940f")}
          </p>
          {stopRequest.message ? <p tabIndex={0} className="max-h-20 overflow-auto text-muted">{noticeText(stopRequest.message)}</p> : null}
          {stopRequest.phase === 'unconfirmed' ? <p className="text-muted">{t("ui.components.chat.Composer.005cc4df6953")}</p> : null}
        </div> : null}

        <SteeringDock key={'steering-' + draftKey} sessionId={draftKey} receipts={steering} onRestored={() => taRef.current?.focus()} />
        {hint ? (
          <div role="status"
            className={cn(
              'mb-2 flex items-center gap-1.5 px-1 text-2xs',
              disconnected ? 'text-warn' : 'text-brand',
            )}
          >
            {disconnected ? <WifiOff className="h-3.5 w-3.5" /> : <ListChecks className="h-3.5 w-3.5" />}
            {hint}
          </div>
        ) : null}

        {sessionId ? <QueuedFollowups key={'followups-' + sessionId} sessionId={sessionId} disabled={controlBusy || disconnected} preserveDraft={hasContent}
          onWorking={setQueueWorking} onRestored={(entry, restoreFocus) => {
            useDraftStore.getState().restoreQueuedText(sessionId, entry.text, entry.attachment_refs)
            if (entry.team_route) useDraftStore.getState().setTeamAddress(sessionId, queuedTeamAddress(entry.team_route))
            if (!restoreFocus || useAppStore.getState().activeSessionId !== sessionId) return
            scheduleFrame(() => {
              const node = taRef.current
              if (!node || useAppStore.getState().activeSessionId !== sessionId) return
              node.focus(); node.setSelectionRange(node.value.length, node.value.length)
            })
          }} /> : null}
        {sessionId && <SessionMemberDock key={'members-' + sessionId} sessionId={sessionId} />}
        {goalPanel}

        <div className="composer-status-rail">
          <GenerationDock key={'generation-' + draftKey} generation={waitingInput ? null : generation} connection={connection} stopping={stopping} />
          <TaskDock key={'tasks-' + draftKey} todos={todos} />
        </div>

        <ComposerCard ref={wrapRef} disconnected={disconnected}>

          {draftError ? <p role="status" className="text-xs text-muted">{noticeText(draftError)}</p> : null}
          {submissions.length > 0 ? <div aria-label={t("ui.components.chat.Composer.d047a381b5a0")} className="max-h-44 space-y-2 overflow-y-auto px-3 pt-2">
            {submissions.map((item) => <div key={item.id} className="space-y-1 rounded-lg border border-line bg-raised p-2 text-xs">
              <p role="status" className={item.status === 'rejected' ? 'text-danger' : 'text-muted'}>{item.status === 'sending' ? t("ui.components.chat.Composer.5b3bebc664b7") : item.status === 'rejected' ? t("ui.components.chat.Composer.404c53e9085e") : item.status === 'unapplied' ? t("ui.components.chat.Composer.353143d3aca3") : t("ui.components.chat.Composer.8aaf425eb291")}</p>
              <p className="line-clamp-2 whitespace-pre-wrap">{item.text || t("ui.components.chat.Composer.402c881d6d0b")}</p>
              {item.error ? <p className="text-danger">{noticeText(item.error)}</p> : null}
              {item.status !== 'sending' ? <>
                {item.status === 'unknown' ? <p className="text-muted">{t("ui.components.chat.Composer.7e94c90e9086")}</p> : null}
                <Button size="xs" disabled={hasContent || attachments.drafts.length > 0} onClick={() => { if (useDraftStore.getState().restoreSubmission(draftKey, item.id)) taRef.current?.focus() }}>{item.status === 'unknown' ? t("ui.components.chat.Composer.27b77a72ed57") : t("ui.components.chat.Composer.42f3afc667d4")}</Button>
                <Button size="xs" onClick={() => useDraftStore.getState().settleSubmission(draftKey, item.id)}>{t("ui.components.chat.Composer.081ed577f0c2")}</Button>
                {hasContent || attachments.drafts.length > 0 ? <p className="text-muted">{t("ui.components.chat.Composer.9b2fe1f66f6f")}</p> : null}
              </> : null}
            </div>)}
          </div> : null}
          <ComposerAttachments attachments={attachments} pickerRef={pickerRef} />

          {(agentMount?.agent_type === 'team' || teamAddress) && <TeamRecipientPicker mount={agentMount} address={teamAddress} onChange={address => useDraftStore.getState().setTeamAddress(draftKey, address)} />}

            <ComposerEditor
            ref={taRef}
            value={value}
            onChange={(event) => {
              // Capture the edit before the external draft store can render
              // the controlled textarea and change its DOM caret/value.
              const text = event.target.value
              const next = detectTrigger(text, event.target.selectionStart ?? text.length)
              setValue(text)
              setTrigger(next ? { ...next, owner: draftKey } : null)
            }}
            onKeyUp={(event) => {
              // Changes arrive via onChange. Only caret movement needs another
              // detection pass; menu-owned key releases must not reopen/reset it.
              if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
              if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)
                  || (!trigger && ['ArrowUp', 'ArrowDown'].includes(event.key))) syncTrigger()
            }}
            onClick={syncTrigger}
            onKeyDown={onKeyDown}
            onPaste={(event) => {
              const clipboard = event.clipboardData
              const files = Array.from(clipboard?.files ?? [])
              const text = clipboard?.getData('text/plain') ?? ''
              if (files.some(file => file.type.startsWith('image/'))) {
                // 默认粘贴在事件返回后执行，必须在读取图片头部之前同步接管。
                if (!text.trim()) event.preventDefault()
                void attachments.addPasted(files)
                return
              }
              if (text && onPaste) onPaste(text)
            }}
            rows={1}
            spellCheck={false}
            placeholder={placeholder}
            aria-label={t("ui.components.chat.Composer.a410452649b3")}
            aria-autocomplete="list"
            aria-haspopup="listbox"
            aria-controls={trigger && items.length ? triggerMenuId : undefined}
            aria-activedescendant={trigger && items[activeIndex] ? `${triggerMenuId}-${activeIndex}` : undefined}
            aria-describedby={running ? 'queued-followup-hint' : undefined}
          />

          {trigger ? (
            <TriggerMenu
              id={triggerMenuId}
              anchor={taRef}
              items={items}
              activeIndex={activeIndex}
              onActive={selectCandidate}
              allItems={trigger.kind === 'reference' ? allItems : undefined}
              onShowMore={trigger.kind === 'reference' ? showMoreReferences : undefined}
              title={trigger.kind === 'reference' ? t("ui.components.chat.Composer.e0ec651f0dae") : t("ui.components.chat.Composer.928f87d4507b")}
              emptyText={
                trigger.kind === 'reference'
                  ? t("ui.components.chat.Composer.77229c7e3ccd")
                  : t("ui.components.chat.Composer.556513743902")
              }
              statusText={trigger.kind === 'reference' ? !sessionId ? t("ui.components.chat.Composer.a563fecb500f") : files.loading ? t("ui.components.chat.Composer.7dee6725e60d") : files.result ? fileCatalogStatus(files.result) : undefined : undefined}
              sourceRoot={trigger.kind === 'reference' ? files.result?.root ?? fileRoot : undefined}
              loading={trigger.kind === 'reference' && files.loading}
              errorText={trigger.kind === 'reference' ? files.error : undefined}
              onRetry={trigger.kind === 'reference' && sessionId ? files.retry : undefined}
              onPick={pick}
              onClose={closeTrigger}
            />
          ) : null}

          {/* 工具条：普通文档流一行，与输入区之间一条发丝线，给「输入 / 操作」分区 */}
          <ComposerToolbar>
            <ComposerAddMenu sessionId={sessionId} draft={value} onChange={setValue}
              focusEditor={() => scheduleFrame(() => taRef.current?.focus())}
              onAddFiles={() => pickerRef.current?.click()} mode={mode} modeBusy={Boolean(planSave?.pending)} onSelectMode={selectMode}
              onReference={prefix => {
                const node = taRef.current
                const caret = node?.selectionStart ?? value.length
                const insert = (caret > 0 && !/\s/.test(value[caret - 1]!) ? ' ' : '') + prefix
                const text = value.slice(0, caret) + insert + value.slice(caret)
                const at = caret + insert.length
                setValue(text)
                pendingCompletion.current = { text, caret: at, draftKey }
                const next = detectTrigger(text, at)
                setTrigger(next ? { ...next, owner: draftKey } : null)
              }} />
            {externalAgent ? <Badge title={t('ui.agent.author.externalModelHint')}>{externalAgent === 'codex' ? 'Codex' : externalAgent === 'claude' ? 'Claude Code' : 'OpenCode'}</Badge> : <ModelSeat buttonRef={modelSeatRef} />}

            {modelMenuOpen && !externalAgent
              ? createPortal(
                  <>
                    <div
                      className="fixed inset-0 z-composer-scrim"
                      onClick={() => setModelMenuOpen(false)}
                      aria-hidden
                    />
                    <div style={modelMenuStyle} className="z-menu">
                      <ModelMenu
                        models={models}
                        current={sessionModel || defaultModel}
                        effort={sessionEffort}
                        onClose={() => { setModelMenuOpen(false); modelSeatRef.current?.focus() }}
                        title={t("ui.components.chat.Composer.1afed6a81a25")}
                        menuVisible={modelMenuStyle.visibility === 'visible'}
                      />
                    </div>
                  </>,
                  document.body,
                )
              : null}

            <PermissionMenu connection={connection} />
            {mode !== 'normal' && <span className="group/composer-item relative inline-flex">
              <Tooltip content={t(mode === 'plan' ? planMode ? 'ui.components.chat.Composer.77bdeab7a5b5' : 'ui.composerMode.planHint' : 'ui.composerMode.goalHint')}>
                <span tabIndex={0} className="inline-flex h-8 items-center gap-1.5 rounded-xl bg-brand/10 px-2.5 text-xs font-medium text-brand focus-visible:outline-brand">
                  {mode === 'plan' ? <ListChecks className="h-4 w-4" aria-hidden="true" /> : <Target className="h-4 w-4" aria-hidden="true" />}
                  {t(mode === 'plan' ? 'ui.composerMode.plan' : 'ui.composerMode.goal')}
                </span>
              </Tooltip>
              <ComposerRemoveButton aria-label={t(mode === 'plan' ? 'ui.composerMode.removePlan' : 'ui.composerMode.removeGoal')}
                onClick={() => selectMode('normal')} disabled={Boolean(planSave?.pending)} className="-right-1 -top-1" />
            </span>}

            {queued > 0 ? (
              <Badge tone="warn" icon={queuePaused ? <Pause className="h-3 w-3" /> : <Loader2 className="h-3 w-3 animate-spin" />}>{t('ui.composerFeedback.queued', { count: queued })}
              </Badge>
            ) : null}

            <div className="ml-auto flex items-center gap-2">
              <ScreenshotButton addFiles={attachments.addFiles} focusEditor={() => {
                if (useAppStore.getState().activeSessionId === sessionId) taRef.current?.focus()
              }} />
              {running ? (
                <Button
                  size="md"
                  variant="secondary"
                  className="h-9 w-9 rounded-full p-0"
                  onClick={onCancel}
                  disabled={stopBusy}
                  aria-label={stopBusy ? t("ui.components.chat.Composer.af48159f380f") : t("ui.components.chat.Composer.ca4d973c0b00")}
                >
                  {stopBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Square className="h-3.5 w-3.5 fill-current" />}
                </Button>
              ) : null}
              {voiceSupported && <VoiceButton editor={taRef} disabled={disconnected || controlBusy} onTranscript={text => {
                const draft = useDraftStore.getState().bySession[draftKey]?.text ?? ''
                setValue(draft + (draft && !/\s$/.test(draft) ? ' ' : '') + text)
                scheduleFrame(() => {resize(); taRef.current?.focus()})
              }} />}
              {!running || hasContent ? (
                <ComposerSendButton
                  disabled={!canSend || controlBusy}
                  onClick={() => submit()}
                  aria-label={running ? t('ui.composerSteering.queue') : t("ui.components.chat.Composer.edecf0ae6e51")}
                  title={running ? t("ui.components.chat.Composer.ee17850f892a") : queuePaused ? t("ui.components.chat.Composer.00ff2981ce60") : t("ui.components.chat.Composer.edecf0ae6e51")}
                />
              ) : null}
            </div>
          </ComposerToolbar>
        </ComposerCard>

        {!sessionId && <NewConversationProject />}
        {showSuggestions && !running && queued === 0 && !value && attachments.drafts.length === 0 && submissions.length === 0 && <ConversationSuggestions disabled={controlBusy} onChoose={text => {
          const state = useAppStore.getState()
          const draft = useDraftStore.getState().bySession[draftKey]
          if (state.activeSessionId !== sessionId || state.newSessionRevision !== newSessionRevision || state.sessionCreating || draft?.text || draft?.attachments.length) return
          setValue(text)
          pendingCompletion.current = {text, caret: text.length, draftKey}
        }} />}

        {running ? <span id="queued-followup-hint" className="sr-only">{t("ui.components.chat.Composer.49bc7900e665")}</span> : null}

      </ComposerDock>
    </>
  )
}
