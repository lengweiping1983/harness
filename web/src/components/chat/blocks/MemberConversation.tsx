import { teamAuthorAvatar } from '@/lib/teamAuthor'
import type { Block } from '@/types'
import { t, useLocale } from '@/i18n'
import { AssistantBlock, UserBlock, ThinkingBlock, NoticeBlock, ErrorBlock, CompactionBlock } from './MessageBlocks'
import { ToolBlock } from './ToolBlock'
import type { ToolResultScope } from './ToolResultReader'

/** 使用主会话相同的消息组件，只呈现真实保存的任务、回复和执行任务。 */
export function MemberConversation({ blocks, name, agent, role, leadName, leadAgent, sessionId, resultScope }: { blocks: Block[]; name: string; agent?: string; role?: string; leadName?: string; leadAgent?: string; sessionId: string; resultScope?: ToolResultScope }) {
  useLocale()
  return <div role="log" aria-label={t('ui.member.conversation')} className="space-y-2">
    {blocks.map(block => {
      if (block.kind === 'user') {
        const message = <UserBlock block={block} speakerLabel={leadName || t('ui.team.author.lead')} speakerAvatar={leadAgent ? teamAuthorAvatar({ team: 'member-conversation', member: 'lead', display_name: leadName || '', role: 'lead', stage: 'reply', agent: leadAgent }) : undefined} hideActions />
        return block.text.length > 600 ? <details key={block.id} className="group"><summary className="activity-summary cursor-pointer">{leadName || t('ui.team.author.lead')} · {t('ui.team.member.prompt')}</summary><div className="mt-2">{message}</div></details> : <div key={block.id}>{message}</div>
      }
      if (block.kind === 'assistant') return <AssistantBlock key={block.id} block={{ ...block, author: block.author ?? { team: 'member-conversation', member: name, display_name: name, role: role === 'lead' ? 'lead' : 'member', stage: 'member', agent } }} />
      if (block.kind === 'thinking') return <ThinkingBlock key={block.id} block={{ ...block, author: { team: 'member-conversation', member: name, display_name: name, role: role === 'lead' ? 'lead' : 'member', stage: 'member', agent } }} />
      if (block.kind === 'tool') return <ToolBlock key={block.id} block={block} sessionId={sessionId} resultScope={resultScope} defaultOpen={false} />
      if (block.kind === 'notice') return <NoticeBlock key={block.id} block={block} />
      if (block.kind === 'error') return <ErrorBlock key={block.id} block={block} sessionId={sessionId} />
      if (block.kind === 'compaction') return <CompactionBlock key={block.id} block={block} />
      return null
    })}
  </div>
}
