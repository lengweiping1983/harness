import {afterEach, expect, it, vi} from 'vitest'
import {useComposerSubmission} from '../useComposerSubmission'
import {useAppStore} from '@/store/app'
import {useDraftStore} from '@/store/drafts'

// 只替换调度钩子；提交业务仍经过实际 useComposerSubmission 和草稿存储。
vi.mock('react', async original => ({...await original<typeof import('react')>(),
  useCallback: (callback: unknown) => callback,
  useRef: (current: unknown) => ({current}),
  useState: (value: unknown) => [value, () => {}],
}))
afterEach(() => {vi.restoreAllMocks(); vi.unstubAllGlobals()})
it.each(['unchanged', 'changed', 'round-trip'] as const)('%s checkout owns clearing the original composer draft', async kind => {
  useAppStore.setState({activeSessionId: null, newSessionRevision: 1, newSessionCheckoutRevision: 0})
  useDraftStore.setState({bySession: {__unsaved__: {text: 'Original 用户', attachments: []}}, submissions: {}})
  const checkout = {cwd: '/project', mode: 'local' as const, ref: 'main'}
  let finish!: (result: boolean) => void
  const response = new Promise<boolean>(resolve => {finish = resolve})
  const clearSent = vi.fn(), setValue = vi.fn()
  const {submit} = useComposerSubmission({value: 'Original 用户', draftKey: '__unsaved__', sessionId: null,
    mode: 'normal', hasGoal: false, planMode: false, disconnected: false, canSend: true, controlBusy: false,
    attachments: {readyRefs: [], clearSent} as unknown as Parameters<typeof useComposerSubmission>[0]['attachments'],
    onSend: () => response, selectMode: vi.fn(), setValue, setTrigger: vi.fn(), resize: vi.fn(), captureFrames: () => callback => callback()})
  const sending = submit()
  if (kind !== 'unchanged') useAppStore.getState().setNewSessionCheckout({...checkout, ref: 'feature'})
  if (kind === 'round-trip') useAppStore.getState().setNewSessionCheckout(checkout)
  finish(true)
  await sending
  expect(setValue).toHaveBeenCalledTimes(kind === 'unchanged' ? 1 : 0)
  expect(clearSent).toHaveBeenCalledTimes(kind === 'unchanged' ? 1 : 0)
})
