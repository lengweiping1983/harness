import {afterEach, expect, it, vi} from 'vitest'
// 首次转换在收集阶段完成；每例仍重载模块，隔离未读意图与存储失败状态。
import '../sessionNotifications'

afterEach(() => vi.unstubAllGlobals())
async function fixture(raw?: string) {
  vi.resetModules()
  const values = new Map<string, string>(raw === undefined ? [] : [['harness.task-unread.v1', raw]])
  let fail = false
  const setItem = vi.fn((key: string, value: string) => {if (fail) throw Error('disk unavailable'); values.set(key, value)})
  vi.stubGlobal('localStorage', {getItem: (key: string) => values.get(key) ?? null, setItem})
  vi.stubGlobal('window', {addEventListener: vi.fn(), location: {pathname: '/'}})
  vi.stubGlobal('Notification', undefined)
  const module = await import('../sessionNotifications'), {useAppStore} = await import('../app')
  useAppStore.setState({toasts: [], sessions: [{id: 'background', title: 'User original {text}', created_at: 1}]})
  const complete = (session_id = 'background') => module.notifySessionCompletion({seq: 1, session_id, run_id: 'run', status: 'completed'})
  return {...module, complete, values, setItem, useAppStore, failWrites: (value: boolean) => {fail = value}}
}
it('persists unread through reload, clears on read and keeps source text across language changes', async () => {
  const f = await fixture()
  f.complete(); f.useSessionNotificationStore.setState({unread: []}); f.loadSessionUnread()
  expect(f.useSessionNotificationStore.getState().unread).toEqual(['background'])
  const {setLocale, t} = await import('@/i18n'), {noticeText} = await import('@/lib/noticeText')
  const toast = f.useAppStore.getState().toasts.at(-1)!
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    expect(noticeText(toast.title)).toBe(t('ui.notifications.completed'))
    expect(toast.description).toBe('User original {text}')
  }
  f.markSessionRead('background')
  expect(f.values.get(f.sessionUnreadStorageKey)).toBe('[]')
  expect(f.useSessionNotificationStore.getState().unread).toEqual([])
})
it.each(['{', 'null', '[1]', '["same","same"]'])('preserves corrupt stored markers and retains in-memory unread intent: %s', async raw => {
  const f = await fixture(raw)
  f.loadSessionUnread(); f.complete(); f.complete('second'); f.markSessionRead('background'); f.loadSessionUnread()
  expect(f.values.get(f.sessionUnreadStorageKey)).toBe(raw)
  expect(f.useSessionNotificationStore.getState().unread).toEqual(['second'])
  expect(f.setItem.mock.calls.filter(([key]) => key === f.sessionUnreadStorageKey)).toHaveLength(0)
  expect(f.useAppStore.getState().toasts.filter(toast => typeof toast.title !== 'string' && 'key' in toast.title && toast.title.key === 'ui.notifications.storage')).toHaveLength(1)
})
it('failed writes cannot revive stale markers or discard newer unread intent', async () => {
  const f = await fixture('["original"]')
  f.loadSessionUnread();f.failWrites(true);f.complete();f.markSessionRead('original');f.loadSessionUnread()
  expect(f.useSessionNotificationStore.getState().unread).toEqual(['background'])
  f.failWrites(false);f.complete('second')
  expect(JSON.parse(f.values.get(f.sessionUnreadStorageKey)!)).toEqual(['background', 'second'])
  f.values.set(f.sessionUnreadStorageKey, '{')
  f.markSessionRead('second')
  expect(f.values.get(f.sessionUnreadStorageKey)).toBe('{')
})
it('permission requests happen only on user submission and supported default-permission browsers', async () => {
  const f = await fixture(), requestPermission = vi.fn(async () => 'denied')
  vi.stubGlobal('Notification', {permission: 'default', requestPermission})
  f.prepareTaskNotifications();expect(requestPermission).toHaveBeenCalledOnce()
  vi.stubGlobal('Notification', {permission: 'denied', requestPermission})
  f.prepareTaskNotifications();expect(requestPermission).toHaveBeenCalledOnce()
  vi.stubGlobal('window', {harnessDesktop: {notifyTask: vi.fn()}})
  vi.stubGlobal('Notification', {permission: 'default', requestPermission})
  f.prepareTaskNotifications();expect(requestPermission).toHaveBeenCalledOnce()
})

it('unnamed conversation fallback remains a resource through language changes', async () => {
  const f = await fixture()
  f.useAppStore.setState({sessions: [{id: 'background', created_at: 1}]})
  f.complete()
  const {setLocale,t} = await import('@/i18n'), {noticeText} = await import('@/lib/noticeText')
  for (const locale of ['en','zh-CN','en'] as const) {
    await setLocale(locale)
    expect(noticeText(f.useAppStore.getState().toasts.at(-1)!.description!)).toBe(t('ui.components.sidebar.ProjectSidebar.a91ae6035092'))
  }
})
