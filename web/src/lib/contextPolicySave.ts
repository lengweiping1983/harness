import {isUnconfirmedWrite, request, type ApiResult} from './http'
import type {ResourceText} from '@/types'
import type {ContextManagementSettings as Policy} from './protocol.generated'

export const contextPolicyFields = ['tool_schema_mode', 'context_auto_manage', 'context_working_budget', 'tool_result_max_chars', 'summary_max_tokens', 'output_reserve', 'context_window'] as const
export type ContextPolicySnapshot = {config: Policy; effective?: {context_window: number | null}}
type Intent = Partial<Policy>
type Attempt = {id: number; owner: object; draftRevision: number; body: Intent; phase: 'saving' | 'unknown' | 'failed' | 'saved'; action: 'write' | 'check'; error: ResourceText; snapshot?: ContextPolicySnapshot}
type Transport = <T>(path: string, options?: {method?: string; body?: string; timeout?: number}) => Promise<ApiResult<T>>

/** 操作记录属于本窗口的全局设置，组件卸载和草稿变化不能释放未知提交。 */
export function createContextPolicySaves(transport: Transport = request, announce: (snapshot: ContextPolicySnapshot, id: number) => void = () => {}) {
  let state: Attempt | null = null
  let identity = 0, notificationEpoch = 0
  const listeners = new Set<() => void>()
  const publish = (next: Attempt | null) => {state = next; listeners.forEach(listener => listener())}
  const busy = () => state?.phase === 'saving'
  const check = async (attempt: Attempt, epoch: number) => {
    const result = await transport<ContextPolicySnapshot>('/api/v1/settings')
    if (state?.id !== attempt.id) return
    if (notificationEpoch !== epoch) {
      publish({...attempt, phase: 'unknown', error: {key: 'ui.context.saveSuperseded'}})
    } else if (result.ok && Object.entries(attempt.body).every(([key, value]) => result.data.config[key as keyof Policy] === value)) {
      const {tool_schema_mode, context_auto_manage, context_working_budget, tool_result_max_chars, summary_max_tokens, output_reserve, context_window} = result.data.config
      const snapshot: ContextPolicySnapshot = {config: {tool_schema_mode, context_auto_manage, context_working_budget, tool_result_max_chars, summary_max_tokens, output_reserve, context_window}, effective: result.data.effective}
      publish({...attempt, phase: 'saved', error: '', snapshot})
      announce(snapshot, attempt.id)
    } else {
      publish({...attempt, phase: 'unknown', error: result.ok ? {key: 'ui.context.saveUnknown'} : result.copy ?? result.message})
    }
  }
  return {
    subscribe: (listener: () => void) => {listeners.add(listener); return () => {listeners.delete(listener)}},
    getSnapshot: () => state,
    notified: () => {++notificationEpoch},
    clearResolved: () => {if (state && ['saved', 'failed'].includes(state.phase)) publish(null)},
    async save(body: Intent, owner: object, draftRevision: number) {
      if (busy() || state?.phase === 'unknown' || !Object.keys(body).length) return
      const epoch = notificationEpoch
      const attempt: Attempt = {id: ++identity, owner, draftRevision, body: Object.freeze({...body}), phase: 'saving', action: 'write', error: ''}
      publish(attempt)
      const result = await transport('/api/v1/settings', {method: 'PATCH', body: JSON.stringify(attempt.body), timeout: 65000})
      if (!result.ok && !isUnconfirmedWrite(result)) {
        publish({...attempt, phase: 'failed', error: result.copy ?? result.message})
        return
      }
      await check(attempt, epoch)
    },
    async recheck() {
      if (state?.phase !== 'unknown') return
      const attempt: Attempt = {...state, phase: 'saving', action: 'check', error: ''}
      const epoch = notificationEpoch
      publish(attempt)
      await check(attempt, epoch)
    },
  }
}

export const contextPolicySaves = createContextPolicySaves(request, (snapshot, id) => {
  window.dispatchEvent(new CustomEvent('harness-context-policy-saved', {detail: {...snapshot, context_save_id: id}}))
})
const contextPolicySaved = () => contextPolicySaves.notified()
if (typeof window !== 'undefined') {
  window.addEventListener('harness-context-policy-saved', contextPolicySaved)
  if (import.meta.hot) import.meta.hot.dispose(() => window.removeEventListener('harness-context-policy-saved', contextPolicySaved))
}