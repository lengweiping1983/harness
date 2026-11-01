import { noticeText } from '@/lib/noticeText'
import { useLocale } from '@/i18n'
import { HookActivityList } from './blocks/HookActivity'
import { Loader2 } from 'lucide-react'
import { memo } from 'react'
import type { Block } from '@/types'
import { ToolBlock } from './blocks/ToolBlock'
import { TodoBlock } from './blocks/TodoBlock'
import { QuestionCard } from './blocks/QuestionCard'
import { DeliverablesCard } from './blocks/DeliverablesCard'
import { questionSubmissionKey, type QuestionSubmissionFeedback } from '@/lib/questionSubmission'
import {
  AssistantBlock,
  CompactionBlock,
  ErrorBlock,
  NoticeBlock,
  SkillBlock,
  SubagentBlock,
  TeamBlock,
  ThinkingBlock,
  TurnBlock,
  UserBlock,
} from './blocks/MessageBlocks'

/**
 * block -> 组件的唯一映射表。
 */

export interface BlockContext {
  sessionId: string | null
  readOnly?: boolean
  questionReadOnly?: boolean
  answerFeedback?: Record<string, QuestionSubmissionFeedback | undefined>
  onAnswer: (questionId: string, answers: unknown) => void
  /** 按会话与问题隔离的提交锁，使用 questionSubmissionKey 生成键。 */
  busyQuestionKeys?: ReadonlySet<string>
  /** 工具卡片默认展开（出错时自动展开更好排查）。 */
  defaultOpenTools?: boolean
}

export const MessageRow = memo(function MessageRow({
  block,
  context,
  hideActions,
}: {
  block: Block
  context: BlockContext
  hideActions?: boolean
}) {
  useLocale()

  switch (block.kind) {
    case 'hook':
      return <HookActivityList rows={block.hooks} sessionId={context.sessionId} />
    case 'user':
      return <UserBlock key={JSON.stringify([context.sessionId, block.id, block.sourceSeq])} block={block} sessionId={context.sessionId} readOnly={context.readOnly} />

    case 'assistant':
      return <AssistantBlock block={block} hideActions={hideActions} />

    case 'thinking':
      return <ThinkingBlock key={JSON.stringify([context.sessionId, block.id])} block={block} />

    case 'tool':
      return (
        <ToolBlock
          key={JSON.stringify([context.sessionId, block.id, block.callId])}
          block={block}
          defaultOpen={context.defaultOpenTools}
          sessionId={context.sessionId}
        />
      )

    case 'todo':
      return <TodoBlock todos={block.todos} />

    case 'question':
      return (
        <QuestionCard
          key={JSON.stringify([context.sessionId, block.questionId])}
          sessionId={context.sessionId}
          readOnly={context.questionReadOnly ?? context.readOnly}
          submissionFeedback={context.answerFeedback?.[questionSubmissionKey(context.sessionId, block.questionId)]}
          block={block}
          onAnswer={context.onAnswer}
          busy={context.busyQuestionKeys?.has(questionSubmissionKey(context.sessionId, block.questionId))}
        />
      )

    case 'compaction':
      return <CompactionBlock block={block} />

    case 'subagent':
      return <SubagentBlock block={block} />

    case 'team':
      return <TeamBlock block={block} sessionId={context.sessionId} compact />

    case 'notice':
      return block.id.startsWith('live-feedback:') ? <div role="status" className="flex items-center gap-2 py-2 text-sm text-muted"><Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin text-brand" /><span>{noticeText(block.text)}</span></div> : <NoticeBlock block={block} />

    case 'error':
      return <ErrorBlock block={block} sessionId={context.sessionId} readOnly={context.readOnly} />

    case 'skill':
      return <SkillBlock block={block} />

    case 'deliverables':
      return <DeliverablesCard block={block} />

    case 'turn':
      return <TurnBlock block={block} sessionId={context.sessionId} />

    default:
      return null
  }
})
