import {noticeText} from './noticeText'
import {hasTranslation, t} from '@/i18n'
import type {ResourceCopy} from '@/types'
import type {ApiFailure} from './http'

export type CollaborationFeedback = Pick<ApiFailure, 'code' | 'message' | 'copy'> | {key: string} | null

/** 只根据已注册的产品错误码声明资源，不匹配服务端正文。 */
export function collaborationErrorCopy(code: string): ResourceCopy | undefined {
  const key = 'ui.collaboration.error.' + code
  return hasTranslation(key) ? {key, params: {code}} : undefined
}

/** 服务与浏览器共用公开错误资源，访客切换语言不修改宿主的模型语言。 */
export function collaborationErrorMessage(code: string, fallback?: string): string {
  const copy = collaborationErrorCopy(code)
  return copy ? t(copy.key, copy.params) : fallback || t('ui.collaboration.operation_failed', {code})
}

/** 保存错误身份，到呈现时才解析语言；第三方正文按原文保留。 */
export function collaborationFeedbackMessage(feedback: CollaborationFeedback): string {
  if (!feedback) return ''
  return 'key' in feedback ? t(feedback.key) : feedback.copy ? noticeText(feedback.copy) : collaborationErrorMessage(feedback.code, feedback.message)
}
