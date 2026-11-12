import {memo, type ComponentProps} from 'react'
import {MessageRow} from './MessageList'

/** 对话是核心工作区；进入已有对话时直接呈现消息卡片，避免逐条替换加载占位。 */
export const DeferredMessageRow = memo(function DeferredMessageRow(props: ComponentProps<typeof MessageRow>) {
  return <MessageRow {...props} />
})
