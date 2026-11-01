import {t} from '@/i18n'
import {ApiError} from './http'
import {collaboration} from './collaboration'
import {requestKey} from './sharedAccess'

type Attempt = {request_id: string; expected_revision: string; answers: unknown; retry: boolean}
const memory = new Map<string, Attempt>()
const scope = (session: string, subject: string, question: string) => 'harness.decision.' + JSON.stringify([session, subject, question])
function read(key: string): Attempt | undefined {
  if (memory.has(key)) return memory.get(key)
  try {
    const row = JSON.parse(sessionStorage.getItem(key) ?? 'null')
    if (row && /^[a-f0-9]{32}$/.test(row.request_id) && /^[a-f0-9]{64}$/.test(row.expected_revision)
      && Array.isArray(row.answers) && typeof row.retry === 'boolean') return row
  } catch { /* 当前页面保留内存回执，不因存储不可用自动重发。 */ }
}
function save(key: string, value?: Attempt) {
  if (value) memory.set(key, value)
  else memory.delete(key)
  try {
    if (value) sessionStorage.setItem(key, JSON.stringify(value))
    else sessionStorage.removeItem(key)
  } catch { /* 审批仍受服务端问题锁与请求号保护。 */ }
}
function unconfirmed(message: string): never {
  throw new ApiError({ok: false, status: 0, code: 'decision/unconfirmed', message})
}

/** 写入前冻结请求；未知结果只回查，确认无记录后由下一次点击重试原请求。 */
export async function answerSharedQuestion(session: string, subject: string, question: string, answers: unknown): Promise<void> {
  const key = scope(session, subject, question)
  let attempt = read(key)
  if (attempt) {
    const checked = await collaboration.decision(session, attempt.request_id)
    if (!checked.ok) throw new ApiError(checked)
    if (checked.data.receipt) {save(key); return}
    if (!attempt.retry) {
      save(key, {...attempt, retry: true})
      unconfirmed(t('ui.shared.decisionRetry'))
    }
    if (JSON.stringify(attempt.answers) !== JSON.stringify(answers)) {
      throw new ApiError({ok: false, status: 409, code: 'decision/request-conflict', message: t('ui.shared.decisionChanged')})
    }
  } else {
    const pending = await collaboration.questions(session)
    if (!pending.ok) throw new ApiError(pending)
    const current = pending.data.questions.find(row => row.question_id === question)
    if (!current) throw new ApiError({ok: false, status: 409, code: 'question/conflict', message: t('ui.shared.decisionClosed')})
    attempt = {request_id: requestKey(), expected_revision: current.revision, answers, retry: false}
  }
  save(key, {...attempt, retry: false})
  const submitted = await collaboration.decide(session, question, {request_id: attempt.request_id, expected_revision: attempt.expected_revision, answers: attempt.answers})
  if (submitted.ok) {
    save(key)
    if (submitted.data.resolved === false) throw new ApiError({ok: false, status: 409, code: 'question/conflict', message: t('ui.shared.decisionClosed')})
    return
  }
  const checked = await collaboration.decision(session, attempt.request_id)
  if (checked.ok && checked.data.receipt) {save(key); return}
  if (submitted.status >= 400 && submitted.status < 500 && submitted.status !== 408) {
    save(key)
    throw new ApiError(submitted)
  }
  if (checked.ok) save(key, {...attempt, retry: true})
  unconfirmed(t(checked.ok ? 'ui.shared.decisionRetry' : 'ui.shared.decisionUnknown'))
}
