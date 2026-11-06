import {failureText} from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import {collaborationErrorMessage} from '@/lib/collaborationError'
import {ViewerIdentityContext} from '../ViewerIdentityContext'
import { CompactionProgress } from '@/components/ui/CompactionProgress'
import {MessageEditContext} from '../MessageEditContext'
import { agentSpeakerAvatar } from '@/lib/agentSpeaker'
import { AgentAvatar } from '@/components/ui/AgentAvatar'
import { t, useLocale } from '@/i18n'
import { memo, useContext, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { teamMemberCounts } from '@/lib/teamProgress'
import {
  AlertTriangle,
  Brain,
  ChevronRight,
  CircleCheck,
  CircleStop,
  Info,
  Layers,
  Square,
  Users,
  Zap,
} from 'lucide-react'
import type {
  AssistantBlock as AssistantBlockModel,
  CompactionBlock as CompactionBlockModel,
  ErrorBlock as ErrorBlockModel,
  NoticeBlock as NoticeBlockModel,
  SkillBlock as SkillBlockModel,
  SubagentBlock as SubagentBlockModel,
  TeamBlock as TeamBlockModel,
  ThinkingBlock as ThinkingBlockModel,
  TurnBlock as TurnBlockModel,
  AttachmentInfo,
  ResourceText,
  UserBlock as UserBlockModel,
} from '@/types'
import { cn, formatNumber } from '@/lib/utils'
import {MessageTimestamp} from './MessageTimestamp'
import { formatSize } from '@/lib/attachments'
import { userMessageText } from '@/lib/userMessageText'
import { copyMessageSelection } from '@/lib/copyMessageSelection'
import { api, unwrap } from '@/lib/api'
import { modelFailure } from '@/lib/modelFailure'
import { turnOutcomeCopy } from '@/lib/turnOutcome'
import { formatThinkingText } from '@/lib/thinkingText'
import { useActivityDisclosure } from '@/hooks/useActivityDisclosure'
import { useAppStore } from '@/store/app'
import { useDraftStore } from '@/store/drafts'
import { useChatStore } from '@/store/chat'
import { editedMessageTeamAddress, teamAuthorAvatar } from '@/lib/teamAuthor'
import { normalizeTeamMarkdown } from '@/lib/teamMarkdown'
import { Markdown } from '@/components/markdown/Markdown'
import { CodeBlock } from '@/components/markdown/CodeBlock'
import { isJsonDocument } from '@/lib/jsonDocument'
import { Badge, Button, Dots } from '@/components/ui/primitives'
import { MessageActions } from './MessageActions'
import { TeamDeliveryDetails } from './TeamDeliveryDetails'
import { TeamIntegration } from './TeamIntegration'
import { WorktreeReview } from '@/components/overlays/WorktreeReview'
import { TeamOutput } from './TeamOutput'

/**
 * 消息类 block 的渲染。
 *
 * 这一层只做「把 block 变成好看的 DOM」，管理消息呈现、展开和原位编辑状态：
 * 流式、工具配对、问题卡片收敛全在 `store/chat.ts` 的 reducer 里完成。
 * 组件越薄，重渲染成本越可控 —— 几千条 block 的会话里这是关键。
 */

/* ============================ 用户 ============================ */

type UserProps = { block: UserBlockModel; sessionId?: string | null; speakerLabel?: string; speakerAvatar?: string; hideActions?: boolean; readOnly?: boolean }

export const UserBlock = memo(function UserBlock(props: UserProps) {
  return <UserContent key={JSON.stringify([props.sessionId, props.block.id, props.block.sourceSeq])} {...props} />
})

const UserContent = memo(function UserContent({ block, sessionId, speakerLabel, speakerAvatar, hideActions = false, readOnly = false }: UserProps) {
  useLocale()

  const viewer = useContext(ViewerIdentityContext)
  const other = Boolean(speakerLabel || block.requester && block.requester.subject !== viewer)
  const mounted = useRef(true)
  useLayoutEffect(() => {mounted.current = true; return () => {mounted.current = false}}, [])
  const [settingGoal, setSettingGoal] = useState(false)
  const goalLock = useRef(false)
  const hasOtherGoal = useChatStore(state => Boolean(sessionId && state.sessions[sessionId]?.goalState.goal && state.sessions[sessionId]?.goalState.goal?.phase !== 'complete'))
  const planMode = useChatStore(state => Boolean(sessionId && state.sessions[sessionId]?.planMode))
  const setAsGoal = async () => {
    if (readOnly || !sessionId || block.sourceSeq === undefined || block.goalId || goalLock.current) return
    const current = useChatStore.getState().sessions[sessionId]
    if (current?.planMode || (current?.goalState.goal && current.goalState.goal.phase !== 'complete')) return
    goalLock.current = true; setSettingGoal(true)
    try {
      const next = await unwrap(api.controlGoal(sessionId, {action: 'create', objective: block.text, source_seq: block.sourceSeq, max_goal_rounds: null}))
      useChatStore.getState().apply(sessionId, {type: 'goal/state', ...next})
      if (next.goal) useChatStore.getState().apply(sessionId, {type: 'goal/message', source_seq: block.sourceSeq, goal_id: next.goal.id})
    } catch (error) {
      if (mounted.current) useAppStore.getState().toast({title: {key: 'ui.components.chat.GoalDock.825cfa17d9cd'}, description: failureText(error), tone: 'danger'})
    } finally {goalLock.current = false; if (mounted.current) setSettingGoal(false)}
  }

  const transport = useContext(MessageEditContext)
  const editKey = String(block.sourceSeq ?? block.id)
  const editText = useDraftStore(state => sessionId ? state.bySession[sessionId]?.inlineEdits?.[editKey] : undefined)
  const editing = !readOnly && editText !== undefined
  const editor = useRef<HTMLTextAreaElement>(null)
  const locked = useRef(false)
  const [sending, setSending] = useState(false)
  const [editError, setEditError] = useState<ResourceText | null>(null)
  useLayoutEffect(() => {
    if (!editing || !editor.current) return
    editor.current.style.height = '0px'
    editor.current.style.height = Math.min(editor.current.scrollHeight, 420) + 'px'
  }, [editing, editText])
  const edit = () => {
    if (!sessionId || block.sourceSeq === undefined || editing) return
    setEditError(null)
    useDraftStore.getState().setInlineEdit(sessionId, editKey, block.text)
  }
  const submitEdit = async () => {
    if (!sessionId || locked.current) return
    const pendingEdits = useDraftStore.getState().bySession[sessionId]?.inlineEdits
    const submittedText = pendingEdits?.[editKey]
    if (submittedText === undefined || (!submittedText.trim() && !block.attachments?.length)) return
    const ownsEditor = () => mounted.current && useDraftStore.getState().bySession[sessionId]?.inlineEdits === pendingEdits
    setEditError(null)
    if (!transport || transport.sessionId !== sessionId || transport.channel.connection !== 'open' || useAppStore.getState().activeSessionId !== sessionId) {
      setEditError({key: 'ui.hooks.useSessionSocket.e1e257086621'})
      return
    }
    const address = editedMessageTeamAddress(block.teamRecipients, useChatStore.getState().sessions[sessionId]?.agentMount ?? null)
    const running = useChatStore.getState().sessions[sessionId]?.running
    if (running && address) {
      setEditError({key: 'ui.messageEdit.steeringUnavailable'})
      return
    }
    locked.current = true
    setSending(true)
    try {
      const accepted = await (running ? transport.channel.steer(submittedText.trim(), block.attachments) : transport.channel.send(submittedText.trim(), block.attachments, address))
      if (accepted) {
        if (useDraftStore.getState().bySession[sessionId]?.inlineEdits === pendingEdits) useDraftStore.getState().setInlineEdit(sessionId, editKey)
      } else if (ownsEditor()) setEditError({key: 'ui.hooks.useSessionSocket.e1e257086621'})
    } catch (error) {
      if (ownsEditor()) setEditError(error instanceof Error ? failureText(error) : {key: 'ui.messageEdit.failed'})
    } finally {
      locked.current = false
      if (mounted.current) setSending(false)
    }
  }

  return (
    <article className={cn('group/message relative flex max-w-full flex-col', other ? 'mr-auto items-start' : 'ml-auto items-end', editing && 'w-full')}>
      <div data-user-message-bubble="" className={cn('min-w-0 max-w-full rounded-2xl bg-raised px-4 py-3 sm:max-w-xl', editing && 'ui-input-group w-full border border-line transition-colors sm:max-w-full')}>
        <header className={cn('mb-1 flex flex-wrap items-center gap-2', !other && !block.steeringStatus && 'sr-only')}>
          {speakerLabel && <AgentAvatar src={speakerAvatar} name={speakerLabel} className="h-6 w-6 rounded-full text-2xs" />}
          <span className="text-2xs font-medium text-muted">{speakerLabel || block.requester?.display_name || t(readOnly ? "ui.shared.user" : "ui.components.chat.blocks.MessageBlocks.a0c7716669b5")}</span>
          <MessageTimestamp at={block.at} kind="sent" className="text-2xs text-faint" />

          {block.steeringStatus ? <span className="text-2xs text-brand" title={block.steeringReason || (block.steeringTeamDelivered ? t('ui.steering.teamDeliveredHint') : undefined)}>
            {t(block.steeringStatus === 'pending' && block.steeringTeamDelivered ? 'ui.steering.teamDelivered' : 'ui.messageEdit.steering.' + block.steeringStatus)}
          </span> : null}

        </header>
        {block.teamRecipients && <p className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-brand" title={block.teamRecipients.display_name}>
          <span className="text-muted">{t('ui.team.recipients.label')}</span>
          {block.teamRecipients.members.map(member => <span key={member.name} className="rounded-md bg-brand/10 px-2 py-1 [overflow-wrap:anywhere]">{member.display_name}</span>)}
        </p>}
        {block.attachments?.length ? (
          <MessageAttachments items={block.attachments} readOnly={readOnly} />
        ) : null}
        {editing ? <div>
          <textarea ref={editor} autoFocus aria-label={t('ui.messageEdit.action')} value={editText} disabled={sending}
            className="block min-h-12 w-full resize-none overflow-y-auto rounded-lg border-0 bg-transparent p-0 text-base leading-reading text-ink outline-none disabled:opacity-60"
            onChange={event => sessionId && useDraftStore.getState().setInlineEdit(sessionId, editKey, event.target.value)}
            onKeyDown={event => {
              if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
              if (event.key === 'Escape' && !sending && sessionId) useDraftStore.getState().setInlineEdit(sessionId, editKey)
              if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {event.preventDefault(); void submitEdit()}
            }} />
          {editError && <p role="alert" className="mt-2 text-xs text-danger">{noticeText(editError)}</p>}
          <div className="mt-2 flex justify-end gap-2">
            <Button size="sm" variant="secondary" disabled={sending} onClick={() => sessionId && useDraftStore.getState().setInlineEdit(sessionId, editKey)}>{t('ui.messageEdit.cancelInline')}</Button>
            <Button size="sm" disabled={sending || (!editText.trim() && !block.attachments?.length)} onClick={() => void submitEdit()}>{t('ui.components.chat.Composer.edecf0ae6e51')}</Button>
          </div>
        </div> : <div data-user-message-body="" onCopy={event => copyMessageSelection(event.nativeEvent, event.currentTarget)} className="md text-base leading-reading text-ink">
          <Markdown dense>{userMessageText(block.text)}</Markdown>
        </div>}
      </div>
          {!editing && (!hideActions || block.goalId) && (block.text.trim() || block.attachments?.length) ? (
            <MessageActions
              text={block.text}
              label={t("ui.components.chat.blocks.MessageBlocks.0e514e9350e1")}
              onEdit={!readOnly && sessionId && block.sourceSeq !== undefined ? () => void edit() : undefined}
              editing={editing}
              onSetGoal={!readOnly && !planMode && !hasOtherGoal && sessionId && block.sourceSeq !== undefined && !block.goalId && block.text.trim() ? () => void setAsGoal() : undefined}
              goalId={block.goalId} hideControls={hideActions}
              settingGoal={settingGoal} goalDisabled={hasOtherGoal}
              className={cn("mt-1 select-none", other ? "justify-start" : "justify-end")}
            />
          ) : null}
    </article>
  )
})

/**
 * 已发送消息里的附件。
 *
 * 必须回显：用户发出后需要确认"我确实把那张图发出去了"，而不是靠记忆。
 * 图片走 ``/api/v1/attachments/{id}`` 而不是 data URL —— 内容寻址让这条 URL
 * 不可变，浏览器会永久缓存，滚动历史时不会重新下载；data URL 则会把整个
 * 会话撑大好几 MB。
 */
export const MessageAttachments = memo(function MessageAttachments({ items, readOnly = false }: { items: AttachmentInfo[]; readOnly?: boolean }) {
  useLocale()

  const openGallery = useAppStore(state => state.openImageGallery)
  const images = items.filter(item => item.isImage)
  if (readOnly) return <div className="mb-2 flex flex-wrap gap-2 text-xs text-muted">{items.map(item => <span key={item.attachmentId} className="rounded border border-line px-2 py-1">{item.name}</span>)}</div>
  return (
    <>
      <div className="mb-1.5 flex flex-wrap gap-1.5">
        {items.map((item) =>
          item.isImage ? (
            <button
              key={item.attachmentId}
              type="button"
              onClick={() => openGallery(images.map(image => ({ src: api.attachmentUrl(image.attachmentId), alt: image.name })), images.indexOf(item))}
              title={item.name}
              className="group relative h-16 w-16 overflow-hidden rounded-lg border border-line transition-transform hover:scale-thumbnail"
            >
              <img
                src={api.attachmentUrl(item.attachmentId)}
                alt={item.name}
                loading="lazy"
                decoding="async"
                className="h-full w-full object-cover"
              />
            </button>
          ) : (
            <a
              key={item.attachmentId}
              href={api.attachmentUrl(item.attachmentId)}
              target="_blank"
              rel="noopener noreferrer"
              title={item.name}
              className="flex h-16 w-28 flex-col justify-center gap-0.5 rounded-lg border border-line bg-raised/60 px-2 transition-colors hover:border-brand/40"
            >
              <span className="truncate text-xs font-medium text-ink">{item.name}</span>
              <span className="truncate text-2xs text-faint">
                {item.mediaType.split('/')[1]} · {formatSize(item.size)}
              </span>
            </a>
          ),
        )}
      </div>
    </>
  )
})


/* ============================ 助手 ============================ */

export const AssistantBlock = memo(function AssistantBlock({
  block,
  hideActions,
}: {
  block: AssistantBlockModel
  hideActions?: boolean
}) {
  useLocale()

  const sessionId = useAppStore(state => state.activeSessionId)
  const text = useMemo(() => block.author ? normalizeTeamMarkdown(block.text) : block.text, [block.author, block.text])
  const jsonDocument = useMemo(() => !block.streaming && isJsonDocument(text), [block.streaming, text])
  const partialStatus = t(block.partial === 'stopped' ? 'ui.components.chat.blocks.MessageBlocks.f006455e3baf' : 'ui.components.chat.blocks.MessageBlocks.554707d96341')
  const partialLabel = block.previewOnly ? t('ui.team.preview.partial', {status: partialStatus})
    : block.partial === 'interrupted' ? t('ui.components.chat.blocks.MessageBlocks.b5a51d9b92ab')
    : t('ui.components.chat.blocks.MessageBlocks.c0f77427e674', {p0: partialStatus})
  const partialHint = block.previewOnly ? t('ui.team.preview.partialHint')
    : t(block.partial === 'interrupted' ? 'ui.components.chat.blocks.MessageBlocks.2b2c9b2f6356' : 'ui.components.chat.blocks.MessageBlocks.c32f8be1b09e')


  return (
    <article className="group/message relative pt-1">
      <div className="min-w-0 flex-1">
        {block.speaker && !block.author && <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
          <AgentAvatar src={agentSpeakerAvatar(block.speaker, sessionId)} name={block.speaker.display_name} className="h-8 w-8 rounded-lg" />
          <span className="font-medium text-ink">{block.speaker.display_name}</span>
          {block.speaker.profession && <span className="text-xs text-muted">{block.speaker.profession}</span>}
        </div>}
        {block.author && <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
          <AgentAvatar src={teamAuthorAvatar(block.author, sessionId)} name={block.author.display_name} className="h-8 w-8 rounded-lg" />
          <span className="font-medium text-ink">{block.author.display_name}</span>
          <span className="text-xs text-muted">{t(block.author.role === 'lead' ? 'ui.team.author.lead' : 'ui.team.author.member')}</span>
          {block.author.stage === 'reply' && <span className="rounded-md bg-brand/10 px-2 py-1 text-xs text-brand">{t('ui.team.author.reply')}</span>}
          {block.author.stage === 'summary' && <span className="rounded-md bg-brand/10 px-2 py-1 text-xs text-brand">{t('ui.team.author.summary')}</span>}
          {block.error && <span className="text-xs text-danger">{t('ui.team.author.failed')}</span>}
        </div>}
        {block.previewTruncated && <p className="mb-1 text-xs text-warn">{t('ui.team.preview.truncated')}</p>}
        {block.partial ? (
          <p className="mb-1 text-xs text-muted" title={partialHint}>
            {partialLabel}
          </p>
        ) : null}
        {block.checkpointGap ? <p className="mb-1 text-xs text-warn">{t("ui.components.chat.blocks.MessageBlocks.843f577d07f8")}</p> : null}
        {block.text ? (
          <div
            className={cn(
              'md text-base leading-reading text-ink',
              // 流式时最后一段不带下边距，caret 贴得更紧。
              block.streaming && 'caret-tail',
            )}
          >
            {jsonDocument ? <CodeBlock code={text} language="json" /> : block.author ? <TeamOutput text={text} streaming={block.streaming} /> : <Markdown streaming={block.streaming}>{text}</Markdown>}
            {block.streaming ? <span className="streaming-caret" aria-hidden /> : null}
          </div>
        ) : block.streaming ? (
          <div className="flex items-center gap-2 py-1 text-muted">
            <Dots />
            <span className="text-2xs">{t("ui.components.chat.blocks.MessageBlocks.43ce458cae49")}</span>
          </div>
        ) : null}



        {/* 放在正文之后，悬停或键盘聚焦时出现，避免覆盖上一条工具活动。 */}
        {block.text && !block.streaming && !hideActions ? (
          <MessageActions text={text} label={block.author ? t('ui.team.author.messageLabel', {name: block.author.display_name}) : t("ui.components.chat.blocks.MessageBlocks.c7df1189794a")} className="justify-start" />
        ) : null}
      </div>
    </article>
  )
})

/* ============================ 思考 ============================ */

export const ThinkingBlock = memo(function ThinkingBlock({ block }: { block: ThinkingBlockModel }) {
  const locale = useLocale()

  const sessionId = useAppStore(state => state.activeSessionId)
  const streaming = Boolean(block.streaming)
  const { open, toggle } = useActivityDisclosure(streaming, false, false, JSON.stringify([sessionId, block.id, block.sourceSeq]))
  const readable = useMemo(() => formatThinkingText(block.text), [block.text, locale])
  return (
    <details
      className="group animate-fade-in"
      open={open}
    >
      <summary onClick={(event) => { event.preventDefault(); toggle() }} className="activity-summary conversation-activity cursor-pointer list-none [&::-webkit-details-marker]:hidden">
        {block.author && <AgentAvatar src={teamAuthorAvatar(block.author, sessionId)} name={block.author.display_name} className="h-5 w-5 rounded-full text-2xs" />}
        {block.speaker && !block.author && <><AgentAvatar src={agentSpeakerAvatar(block.speaker, sessionId)} name={block.speaker.display_name} className="h-5 w-5 rounded-full text-2xs" /><span>{block.speaker.display_name}</span></>}
        <Brain aria-hidden className="h-4 w-4 shrink-0 text-brand" />
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
          <span>{t("ui.components.chat.blocks.MessageBlocks.a62acb184f47")}</span>
          {block.partial ? <span className="text-faint">{t("ui.components.chat.blocks.MessageBlocks.9f76fd20a5ba")}</span> : null}
          {streaming ? (
            <span className="inline-flex items-center gap-1 text-brand">
              <Dots />
              <span>{t("ui.components.chat.blocks.MessageBlocks.dc9591e56d50")}</span>
            </span>
          ) : null}
        </span>
        <ChevronRight aria-hidden className="h-3.5 w-3.5 shrink-0 text-faint transition-transform duration-200 group-open:rotate-90" />
      </summary>
      <div className="ml-5 border-l border-line py-3 pl-4">
        {block.checkpointGap ? <p className="mb-1 text-xs text-warn">{t("ui.components.chat.blocks.MessageBlocks.843f577d07f8")}</p> : null}
        <div className="md break-words text-compact leading-reading whitespace-pre-wrap text-muted">
          {readable}
          {streaming ? <span className="streaming-caret" aria-hidden /> : null}
        </div>
      </div>
    </details>
  )
})

/* ============================ 提示 / 错误 / 技能 ============================ */

export const NoticeBlock = memo(function NoticeBlock({ block }: { block: NoticeBlockModel }) {
  useLocale()

  return (
    <div
      className={cn(
        'animate-fade-in flex items-start gap-2 rounded-lg border px-2.5 py-2 text-xs',
        block.tone === 'warn'
          ? 'border-warn/25 bg-warn/8 text-warn'
          : 'border-line bg-raised/40 text-muted',
      )}
    >
      {block.tone === 'warn' ? (
        <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" />
      ) : (
        <Info className="mt-px h-3.5 w-3.5 shrink-0 text-faint" />
      )}
      <span className="min-w-0 flex-1 leading-relaxed">{noticeText(block.text)}</span>
    </div>
  )
})

function viewExecution(sessionId: string | null | undefined, turn?: number) {
  const app = useAppStore.getState()
  if (sessionId && Number.isSafeInteger(turn) && turn! > 0) app.revealRun(sessionId, { turn: turn! })
  else app.setInspectorTab('queue')
}

export const ErrorBlock = memo(function ErrorBlock({ block, sessionId, readOnly = false }: { block: ErrorBlockModel; sessionId?: string | null; readOnly?: boolean }) {
  useLocale()

  const original = block.messageCopy ? noticeText(block.messageCopy) : block.messageKey ? t(block.messageKey) : block.message
  const message = readOnly ? collaborationErrorMessage(block.code || 'run/error') : block.code ? collaborationErrorMessage(block.code, original) : original
  const failure = modelFailure(message, block.code)
  const details = !readOnly && block.details && typeof block.details === 'object'
    ? Object.fromEntries(Object.entries(block.details).filter(([key]) => key !== 'copy')) : null
  const diagnostic = details && Object.keys(details).length ? JSON.stringify(details, null, 2) : null
  const technicalDetails = <details className="mt-2 text-xs text-muted"><summary className="cursor-pointer">{t("ui.components.chat.blocks.MessageBlocks.c26fb419db3c")}</summary>{block.code && <p className="mt-1 font-mono">{block.code}</p>}{failure && <p className="mt-1 whitespace-pre-wrap break-words">{message}</p>}{diagnostic && <pre className="mt-1 whitespace-pre-wrap break-words font-mono leading-code">{diagnostic}</pre>}</details>
  return (
    <div className="animate-slide-up overflow-hidden rounded-lg border border-danger/30 bg-danger/8">
      <div className="flex items-center gap-2 border-b border-danger/20 px-2.5 py-1.5">
        <AlertTriangle className="h-3.5 w-3.5 text-danger" />
        <span className="text-2xs font-semibold tracking-wide text-danger">
          {failure?.title ?? block.code ?? t("ui.components.chat.blocks.MessageBlocks.0bc1fb72ae1b")}
        </span>
        {block.turn != null || block.historical ? <span className="ml-auto text-xs text-muted">{block.turn != null ? t(block.historical ? 'ui.message.historicalFailureTurn' : 'ui.message.failureTurn', {turn: block.turn}) : t('ui.components.chat.blocks.MessageBlocks.590cdc817334')}</span> : null}
      </div>
      {failure ? <div className="px-3 py-2 text-compact leading-5"><p>{failure.hint}</p>{technicalDetails}</div> : <pre className="overflow-x-auto px-3 py-2 font-mono text-xs leading-code whitespace-pre-wrap text-ink/85">
        {message}
      </pre>}
      {!failure && diagnostic ? <div className="px-3 pb-2">{technicalDetails}</div> : null}
      {!readOnly && (block.turn != null || block.historical) ? <div className="px-3 pb-2"><Button size="sm" variant="ghost" onClick={() => viewExecution(sessionId, block.turn)}>{t("ui.components.chat.blocks.MessageBlocks.3af0f63b3faa")}</Button></div> : null}
    </div>
  )
})

export const SkillBlock = memo(function SkillBlock({ block }: { block: SkillBlockModel }) {
  useLocale()

  return (
    <div className="animate-fade-in flex items-center gap-2 rounded-lg border border-brand/25 bg-brand/8 px-2.5 py-1.5">
      <Layers className="h-3.5 w-3.5 text-brand" />
      <span className="text-xs text-ink">{t("ui.components.chat.blocks.MessageBlocks.1c8bee8e10f0")}</span>
      {block.name ? (
        <code className="rounded bg-brand/[0.12] px-1.5 py-0.5 font-mono text-2xs text-brand">
          {block.name}
        </code>
      ) : null}
    </div>
  )
})

/* ============================ 上下文压缩 ============================ */

export const CompactionBlock = memo(function CompactionBlock({
  block,
}: {
  block: CompactionBlockModel
}) {
  useLocale()

  if (block.phase === 'start') {
    return (
      <div className="animate-fade-in flex items-center gap-2 rounded-lg border border-line bg-raised/40 px-2.5 py-2 text-xs text-muted">
        <Dots />
        <CompactionProgress completed={block.completedChunks} total={block.totalChunks} stage={block.stage}
          attempt={block.attempt} maxAttempts={block.maxAttempts} retryInSeconds={block.retryInSeconds} />
      </div>
    )
  }
  const label = <>
      {block.cancelled ? <Square className="h-3.5 w-3.5 shrink-0" /> : block.error ? <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-danger" /> : <CircleCheck className="h-3.5 w-3.5 shrink-0" />}
      <span>{block.cancelled ? t("ui.components.chat.blocks.MessageBlocks.de1224d0c7a8") : block.error ? t("ui.components.chat.blocks.MessageBlocks.1a8ed9671bce") : block.skipped ? t("ui.components.chat.blocks.MessageBlocks.72d4a45b350a") : t("ui.components.chat.blocks.MessageBlocks.72a4f36264be")}</span>
      {block.totalChunks ? <span className="text-faint">{t('ui.message.compactionSections', {completed: block.completedChunks ?? 0, total: block.totalChunks})}</span> : null}
      {block.pruned ? <span className="text-ok/80">{t('ui.message.prunedItems', {count: block.pruned})}</span> : null}
      {block.charsRemoved ? (
        <span className="text-ok/80">{t('ui.message.removedCharacters', {count: formatNumber(block.charsRemoved)})}</span>
      ) : null}
    </>
  return block.summary || block.error ? <details className="group/compaction text-xs text-muted"><summary className="flex cursor-pointer list-none items-center gap-2 py-1 [&::-webkit-details-marker]:hidden"><ChevronRight aria-hidden className="h-3 w-3 shrink-0 group-open/compaction:rotate-90" />{label}</summary><pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words border-l-2 border-line pl-3 text-xs leading-5">{block.error || block.summary}</pre></details>
    : <div className="flex items-center gap-2 py-1 text-xs text-muted">{label}</div>
})

/* ============================ 子智能体 ============================ */

const SUBAGENT_TONE: Record<string, string> = {
  pending: 'text-muted border-line bg-raised/40',
  unknown: 'text-muted border-line bg-raised/40',
  interrupted: 'text-warn border-warn/25 bg-warn/8',
  running: 'text-brand border-brand/25 bg-brand/8',
  done: 'text-ok border-ok/25 bg-ok/8',
  failed: 'text-danger border-danger/25 bg-danger/8',
}

export const SubagentBlock = memo(function SubagentBlock({ block }: { block: SubagentBlockModel }) {
  useLocale()

  const owner = useAppStore(state => state.activeSessionId)
  const member = useAppStore(state => state.inspectorOpen && state.inspectorTab.startsWith('member:') ? state.inspectorMemberTarget : null)
  const selected = member?.owner === owner && member?.id === block.agentId
  const status = block.status === 'completed' ? 'done' : ['pending', 'running', 'failed', 'interrupted'].includes(block.status) ? block.status : 'unknown'
  const tone = SUBAGENT_TONE[status] ?? SUBAGENT_TONE.unknown!

  return (
    <div
      className={cn(
        'animate-fade-in overflow-hidden rounded-lg border',
        tone,
        selected && 'ring-1 ring-brand/60',
      )}
    >
      <button
        type="button"
        onClick={event => { if (owner) useAppStore.getState().revealMember({ owner, id: block.agentId, name: block.description || block.agentId, preview: block.result ? { text: block.result, truncated: false } : undefined }, event.currentTarget) }}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs"
        disabled={!owner}
        aria-expanded={selected}
        aria-controls={selected ? 'inspector-tab-content' : undefined}
      >
        <Users className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 flex-1 truncate font-medium">
          {block.description || t("ui.components.chat.blocks.MessageBlocks.83a21da599e9")}
        </span>
        <Badge tone={status === 'done' ? 'ok' : status === 'failed' ? 'danger' : status === 'interrupted' ? 'warn' : status === 'running' ? 'brand' : 'neutral'}>{t('ui.member.status.' + status)}</Badge>
        <ChevronRight aria-hidden className="h-3 w-3 shrink-0" />
      </button>

    </div>
  )
})

/* ============================ 团队 ============================ */

const TEAM_PHASE_COPY: Record<TeamBlockModel['phase'], string> = {
  get start() { return t("ui.components.chat.blocks.MessageBlocks.f0f5d4ead48a") },
  get plan() { return t("ui.components.chat.blocks.MessageBlocks.9b17561bb81a") },
  get execute() { return t("ui.components.chat.blocks.MessageBlocks.d3adff83d3e8") },
  get summary() { return t("ui.components.chat.blocks.MessageBlocks.8fe4a3bf8852") },
  get done() { return t("ui.components.chat.blocks.MessageBlocks.4c37b8974b43") },
  get failed() { return t("ui.components.chat.blocks.MessageBlocks.56b174d71d44") },
  get interrupted() { return t("ui.team.interrupted") },
}

const TEAM_MEMBER_TONE: Record<string, string> = {
  pending: 'border-line bg-raised/40 text-faint',
  running: 'border-brand/25 bg-brand/8 text-brand',
  done: 'border-ok/25 bg-ok/8 text-ok',
  failed: 'border-danger/25 bg-danger/8 text-danger',
  interrupted: 'border-warn/25 bg-warn/8 text-warn',
  blocked: 'border-warn/25 bg-warn/8 text-warn',
}

const TEAM_MEMBER_ICON: Record<string, string> = {
  pending: '…',
  running: '▶',
  done: '✔',
  failed: '✘',
  interrupted: '■',
  blocked: '■',
}

export const TeamBlock = memo(function TeamBlock({ block, sessionId, compact = false }: { block: TeamBlockModel; sessionId?: string | null; compact?: boolean }) {
  useLocale()

  const [open, setOpen] = useState(false)
  const memberTarget = useAppStore(state => state.inspectorOpen && state.inspectorTab.startsWith('member:') ? state.inspectorMemberTarget : null)
  const failed = block.phase === 'failed'
  const interrupted = block.phase === 'interrupted'
  const running = !failed && !interrupted && block.phase !== 'done'
  const memberProblems = block.members.filter(member => member.error || ['failed', 'blocked', 'interrupted'].includes(member.status))
  const partial = block.phase === 'done' && memberProblems.length > 0
  const copy = partial ? t('ui.team.completedWithProblems') : block.phase === 'done' && block.decision === 'reply' ? t('ui.team.replyCompleted') : TEAM_PHASE_COPY[block.phase] ?? block.phase
  const counts = teamMemberCounts(block.members)
  const hasSummary = Boolean(block.summary || block.error)
  const automaticSummary = block.phase === 'done' || failed || interrupted || Boolean(block.error)
  const hasDetails = Boolean(block.worktrees?.length || Object.keys(block.assignments ?? {}).length || hasSummary && !automaticSummary)

  return (
    <div
      className={cn(
        'animate-fade-in overflow-hidden rounded-lg border',
        failed ? 'border-danger/25 bg-danger/5' : interrupted || partial ? 'border-warn/25 bg-warn/5' : running ? 'border-brand/25 bg-brand/5' : 'border-ok/25 bg-ok/5',
      )}
    >
      {hasDetails ? <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2 px-2.5 py-2 text-left text-xs"
        aria-expanded={open}
      >
        <ChevronRight className={cn('h-3 w-3 shrink-0 transition-transform duration-200', open && 'rotate-90')} />
        <Users className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 flex-1 font-medium [overflow-wrap:anywhere]">{block.displayName || block.team || t("ui.components.chat.blocks.MessageBlocks.5d682392c70f")}</span>
      </button> : !compact ? <div className="flex items-start gap-2 px-2.5 py-2 text-xs">
        <Users className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 flex-1 font-medium [overflow-wrap:anywhere]">{block.displayName || block.team || t("ui.components.chat.blocks.MessageBlocks.5d682392c70f")}</span>
      </div> : null}
      {!compact && <div className="flex flex-wrap items-center gap-1.5 px-3 pb-2">
        {block.fallback ? <Badge tone="warn">{t("ui.components.chat.blocks.MessageBlocks.27b592897d05")}</Badge> : null}
        {block.retried && !block.fallback ? <Badge tone="neutral">{t("ui.components.chat.blocks.MessageBlocks.aebf198f2918")}</Badge> : null}
        <Badge tone={failed ? 'danger' : interrupted || partial ? 'warn' : running ? 'brand' : 'ok'}>
          {failed || partial ? <AlertTriangle className="h-2.5 w-2.5" /> : running ? <Dots /> : <CircleCheck className="h-2.5 w-2.5" />}
          {copy}
        </Badge>
        {counts.total > 0 && <span className="text-2xs text-muted">{t('ui.team.count.settled', { count: counts.settled, total: counts.total })}</span>}
      </div>}
      {!compact && block.decisionReason && <p className="border-t border-current/10 px-3 py-2 text-xs leading-relaxed text-muted [overflow-wrap:anywhere]">{block.decisionReason}</p>}
      {!compact && block.task ? (
        <p className="border-t border-current/10 px-3 py-1.5 text-2xs leading-relaxed text-muted [overflow-wrap:anywhere]">{t('ui.message.teamTask', {task: block.task})}
        </p>
      ) : null}
      {!compact && block.members.length ? (
        <div className="flex flex-wrap gap-1.5 border-t border-current/10 px-3 py-2">
          {block.members.map((member) => {
            const label = member.displayName || member.name
            const status = Object.hasOwn(TEAM_MEMBER_TONE, member.status) ? member.status : 'unknown'
            const selectable = Boolean(sessionId && (member.agent_id || member.planning_agent_id))
            const selected = memberTarget?.owner === sessionId && memberTarget?.id === (member.agent_id || member.planning_agent_id)
            const avatar = teamAuthorAvatar({ team: block.team, member: member.name, display_name: label, role: member.role === 'lead' ? 'lead' : 'member', stage: 'member', ...(member.agent ? { agent: member.agent } : {}) }, sessionId)
            const dependencies = member.depends_on?.map(name => block.members.find(row => row.name === name)?.displayName || name).join(t('ui.common.list_separator')) ?? ''
            const title = (member.error ? t('ui.team.member.errorTitle', { name: label, error: member.error }) : t('ui.team.member.roleTitle', { name: label, role: ['lead', 'member'].includes(member.role) ? t('ui.team.member.role.' + member.role) : member.role })) + (dependencies ? ` ← ${dependencies}` : '')
            const body = <>
              <AgentAvatar src={avatar} name={label} className="h-6 w-6 rounded-md text-2xs" />
              <span aria-hidden>{TEAM_MEMBER_ICON[status] ?? '…'}</span>
              {label}
              <span className="text-faint">{t('ui.member.status.' + status)}</span>
              {member.runner && <span className="text-faint">· {member.runner === 'claude' ? 'Claude Code' : member.runner === 'codex' ? 'Codex' : 'OpenCode'}</span>}
              {member.depends_on?.length ? <span className="max-w-full text-faint">← {dependencies}</span> : null}
            </>
            const tone = cn(
              'inline-flex min-w-0 max-w-full flex-wrap items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-2xs [overflow-wrap:anywhere]',
              TEAM_MEMBER_TONE[status] ?? TEAM_MEMBER_TONE.pending!,
            )
            if (!selectable) return <span key={member.name} title={title} className={tone}>{body}</span>
            return <button
              key={member.name}
              type="button"
              title={title}
              aria-expanded={selected}
              onClick={event => {
                if (!sessionId) return
                const id = member.agent_id || member.planning_agent_id
                if (id) useAppStore.getState().revealMember({ owner: sessionId, id, name: label, contextSessionId: member.context_session_id, preview: block.previews?.[member.name] }, event.currentTarget)
              }}
              className={cn(tone, 'cursor-pointer text-left transition-colors hover:brightness-110 focus-visible:outline focus-visible:outline-1 focus-visible:outline-brand', selected && 'ring-1 ring-brand/60')}
            >
              {body}
            </button>
          })}
        </div>
      ) : null}
      {open && Boolean(block.worktrees?.length) && <div className="min-w-0 space-y-3 border-t border-current/15 px-3 py-2" aria-label={t('ui.team.worktrees.heading')}>
        <p className="text-xs font-medium">{t('ui.team.worktrees.heading')}</p>
        <p className="text-2xs text-muted">{t(block.worktrees?.some(row => row.source_snapshot) ? 'ui.team.worktrees.sourceNote' : 'ui.team.worktrees.note')}</p>
        {sessionId && <TeamIntegration key={sessionId + ':' + block.runId + ':' + block.sourceSeq} block={block} sessionId={sessionId} />}
        {block.worktrees?.map(worktree => <section key={worktree.id} className="min-w-0 space-y-2 rounded-md border border-line bg-canvas/50 p-2">
          <p className="break-all text-xs font-medium text-brand">{worktree.team_member}</p>
          {worktree.branch && <p className="break-all font-mono text-2xs text-muted">{worktree.branch}</p>}
          <p className="break-all font-mono text-2xs text-muted">{worktree.path}</p>
          {worktree.source_snapshot && <p className={cn('break-words text-2xs', worktree.source_snapshot.status === 'failed' ? 'text-danger' : 'text-muted')}>
            {t('ui.team.worktrees.sourceVersion')} <code title={worktree.source_snapshot.tree}>{worktree.source_snapshot.tree.slice(0, 12)}</code> · {t('ui.team.worktrees.sourceStatus.' + worktree.source_snapshot.status)}
          </p>}
          <TeamDeliveryDetails member={block.members.find(member => member.name === worktree.team_member)} worktrees={block.worktrees ?? []} />
          <WorktreeReview id={worktree.id} />
        </section>)}
      </div>}
      {(open || automaticSummary) && (hasSummary || open && Object.keys(block.assignments ?? {}).length > 0) ? (
        <div className="border-t border-current/15 px-3 py-2">
          {open && Object.keys(block.assignments ?? {}).length ? <div className="mb-3 space-y-2"><p className="text-2xs font-medium text-muted">{t('ui.team.assignments')}</p>{Object.entries(block.assignments ?? {}).map(([name, task]) => <div key={name} className="rounded-md border border-line bg-canvas/50 p-2"><p className="font-mono text-2xs text-brand">{block.members.find(member => member.name === name)?.displayName || name}</p><p className="mt-1 whitespace-pre-wrap text-xs text-muted [overflow-wrap:anywhere]">{task}</p></div>)}</div> : null}
          {block.error ? (
            <pre className="font-mono text-xs whitespace-pre-wrap text-danger">{block.error}</pre>
          ) : (
            <div className="md text-compact leading-reading text-ink/85">
              <Markdown dense>{normalizeTeamMarkdown(block.summary ?? '')}</Markdown>
            </div>
          )}
        </div>
      ) : null}
    </div>
  )
})

/* ============================ 回合边界 ============================ */

export const TurnBlock = memo(function TurnBlock({ block, sessionId }: { block: TurnBlockModel; sessionId?: string | null }) {
  useLocale()

  if (block.phase === 'start') {
    return (
      <div className="flex items-center gap-3 py-0.5">
        <span className="h-px flex-1 bg-line" />
        <span className="inline-flex items-center gap-1.5 text-2xs text-faint">
          <Zap className="h-3 w-3" />{block.turn == null ? t('ui.message.turnStart') : t('ui.message.turnNumber', {turn: block.turn})}</span>
        <span className="h-px flex-1 bg-line" />
      </div>
    )
  }
  const copy = turnOutcomeCopy(block.reason)
  if (!copy) return null
  const stopped = block.reason === 'aborted'
  return (
    <section data-turn-outcome={block.reason} data-turn-number={block.turn} aria-label={copy.title}
      className={cn('rounded-xl border px-3 py-2.5 text-xs', stopped ? 'border-line bg-raised/30 text-muted' : 'border-warn/25 bg-warn/5 text-muted')}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {stopped ? <CircleStop aria-hidden className="h-3.5 w-3.5" /> : <AlertTriangle aria-hidden className="h-3.5 w-3.5 text-warn" />}
        <span className="font-medium text-ink">{copy.title}</span>
        {block.turn != null || block.historical ? <span className="ml-auto text-2xs text-faint">{block.turn != null ? t(block.historical ? 'ui.message.historicalStatusTurn' : 'ui.message.statusTurn', {turn: block.turn}) : t('ui.components.chat.blocks.MessageBlocks.7345d4847180')}</span> : null}
      </div>
      <p className="mt-1.5 leading-5">{copy.hint}</p>
      <Button size="xs" variant="ghost" className="mt-1" onClick={() => viewExecution(sessionId, block.turn)}>{t("ui.components.chat.blocks.MessageBlocks.7715a51ffbac")}</Button>
    </section>
  )
})
