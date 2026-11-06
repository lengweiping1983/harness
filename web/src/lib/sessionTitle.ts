import { t } from '@/i18n'
import type { SessionSummary } from '@/types'

/** 模式命令属于输入控制，自动名称只取任务正文。 */
export function sessionTaskTitle(text?: string | null): string {
  return (text ?? '').trim().replace(/^\/(?:plan|goal)(?:\s+|$)/, '').replace(/\s+/g, ' ').trim()
}

/** 对话标题与侧栏保持一致；显式名称保留原文，摘要去掉输入模式。 */
export function sessionTitle(session: SessionSummary | null | undefined): string {
  return session?.title?.trim() || sessionTaskTitle(session?.summary)
    || t(session?.message_count === 0 && session.metadata?.auto_title === 'awaiting'
      ? 'ui.sessionCreation.emptyDraft' : 'ui.components.sidebar.ProjectSidebar.a91ae6035092')
}
