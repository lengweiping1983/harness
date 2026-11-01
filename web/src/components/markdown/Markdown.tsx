import {memo} from 'react'
import type {MarkdownProps} from './MarkdownContent'
import {Markdown as MarkdownContent} from './MarkdownContent'

export type {MarkdownProps, MarkdownFileSource} from './MarkdownContent'
export {FileChip} from './FileChip'
/** 对话正文是核心内容，进入已有对话时直接呈现，避免正文逐条替换加载占位。 */
export const Markdown = memo(function Markdown(props: MarkdownProps) {
  if (!props.children?.trim()) return null
  return <MarkdownContent {...props} />
})
