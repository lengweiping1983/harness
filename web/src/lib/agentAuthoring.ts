import {ResourceError} from './resourceError'
import {ApiError, isUnconfirmedWrite, runtimeRequest} from './http'
import type {RunRecord, SubmissionStatus} from './protocol.generated'
import {t} from '@/i18n'

export type AuthoringKind = 'skill' | 'plugin'
export type AuthoringSubmission = {id: string; session: string; text: string}

export function authoringText(kind: AuthoringKind, requirements: string): string {
  return `/skill ${kind}-creator ${t('ui.authoring.task.' + kind)}\n\n${t('ui.authoring.requirements')}\n${requirements.trim()}`
}

export async function submitAuthoring(submission: AuthoringSubmission, readOnly = false): Promise<RunRecord> {
  const path = '/sessions/' + encodeURIComponent(submission.session)
  const matches = (run: RunRecord | null) => run?.session_id === submission.session && run.text === submission.text
  if (!readOnly) {
    try {
      const run = await runtimeRequest<RunRecord>(path + '/runs', 'POST', {text: submission.text},
        {headers: {'Idempotency-Key': submission.id, 'X-Request-ID': submission.id}})
      if (matches(run)) return run
    } catch (failure) {
      if (failure instanceof ApiError && !isUnconfirmedWrite(failure.failure)) throw failure
    }
  }
  try {
    const {run} = await runtimeRequest<SubmissionStatus>(path + '/submissions/' + encodeURIComponent(submission.id))
    if (matches(run)) return run!
  } catch { /* 读取失败不能证明原写入被拒绝，保留原请求身份。 */ }
  throw new ResourceError({key: 'ui.authoring.unknown'})
}
