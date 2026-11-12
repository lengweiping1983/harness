import {forwardRef, type ComponentProps, type HTMLAttributes, type ReactNode} from 'react'
import {cn} from '@/lib/utils'
import {Conversation} from './Conversation'

type ConversationPaneProps = Omit<HTMLAttributes<HTMLElement>, 'title' | 'children'> & {
  title: string
  empty?: boolean
  headerLeading?: ReactNode
  headerTrailing?: ReactNode
  titleIndicator?: ReactNode
  notice?: ReactNode
  conversation?: ComponentProps<typeof Conversation>
  children?: ReactNode
}

/** 本机与受邀会话共用中间页面，访问权限由各自的业务适配器提供。 */
export const ConversationPane = forwardRef<HTMLElement, ConversationPaneProps>(function ConversationPane({
  title, empty = false, headerLeading, headerTrailing, titleIndicator, notice, conversation, children, className, ...props
}, ref) {
  return <main {...props} ref={ref} id="conversation-content" className={cn('conversation-pane relative flex min-h-0 min-w-0 flex-1 flex-col', className)} data-empty={empty ? '1' : '0'}>
    <header className="conversation-tab-header" aria-label={title}>
      {headerLeading}
      <div role="tablist" className="min-w-0 flex-1" aria-label={title}><button type="button" role="tab" aria-selected={true} aria-controls="conversation-content" className="flex max-w-full items-center gap-2 text-xs font-medium" title={title}><span className="truncate">{title}</span>{titleIndicator}</button></div>
      {headerTrailing}
    </header>
    {notice}
    <div data-message-navigation-host="" className="conversation-body relative flex min-h-0 flex-1 flex-col">
      {conversation && <Conversation {...conversation} />}
      {children}
    </div>
  </main>
})
