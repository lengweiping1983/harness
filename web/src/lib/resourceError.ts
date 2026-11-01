import { noticeText } from './noticeText'
import type { ResourceCopy, ResourceTemplate, ResourceText } from '@/types'
import { ApiError } from './http'

/** 产品错误保留资源身份，Error.message 仍用于发生时的诊断。 */
export class ResourceError extends Error {
  constructor(readonly copy: ResourceCopy | ResourceTemplate) { super(noticeText(copy)) }
}

/** 只识别明确的产品资源身份，不按异常原文猜测翻译。 */
export function failureText(failure: unknown): ResourceText {
  if (failure instanceof ResourceError) return failure.copy
  if (failure instanceof ApiError) return failure.failure.copy ?? failure.message
  return failure instanceof Error ? failure.message : String(failure)
}
