import {collaborationErrorCopy} from './collaborationError'
import type {ResourceText} from '@/types'

/** 拒绝必须保留非空反馈，空正文不能被草稿仓库当作成功结算。 */
export function submissionErrorText(frame: Record<string, unknown>): ResourceText {
  const copy = typeof frame.code === 'string' ? collaborationErrorCopy(frame.code) : undefined
  if (copy) return copy
  for (const value of [frame.message, frame.error]) {
    if (typeof value === 'string' && value.trim()) return value
  }
  return {key: 'ui.hooks.useSessionSocket.9f22a898502c'}
}
