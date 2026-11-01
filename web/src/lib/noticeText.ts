import { t } from '@/i18n'
import type { NoticeText } from '@/types'
import { permissionDecision } from './permissions'

/** 保存资源身份，展示时按当前语言解析；用户及第三方原文保持原样。 */
export function noticeText(text: NoticeText): string {
  if (typeof text === 'string') return text
  if ('key' in text) return t(text.key, text.params)
  if ('template' in text) return t(text.template.key, Object.fromEntries(Object.entries(text.template.params)
    .map(([key, value]) => [key, typeof value === 'number' ? value : noticeText(value)])))
  const { decisions, reviewer, reason, tool } = text.approval
  return t('ui.store.chat.c36db9f5daed', { p0: permissionDecision(decisions, reviewer, reason), p1: tool })
}
