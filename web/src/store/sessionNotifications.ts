import {create} from 'zustand'
import {t} from '@/i18n'
import {sessionTaskTitle} from '@/lib/sessionTitle'
import {noticeText} from '@/lib/noticeText'
import type {SessionCompletion} from '@/lib/protocol.generated'
import {useAppStore} from './app'

const storageKey = 'harness.task-unread.v1'
export const useSessionNotificationStore = create<{unread: string[]; storageFailed: boolean}>(() => ({unread: [], storageFailed: false}))
const notifications = new Map<string, Notification>()
const pendingUnread = new Map<string, boolean>()

type DesktopNotifications = {
  notifyTask?: (payload: {sessionId: string; title: string; body: string}) => Promise<boolean>
  clearTaskNotification?: (sessionId: string) => Promise<void>
  onTaskNotificationOpen?: (listener: (sessionId: string) => void) => () => void
}
const desktop = () => (window as unknown as {harnessDesktop?: DesktopNotifications}).harnessDesktop

function storedUnread(): string[] {
  const raw = localStorage.getItem(storageKey)
  if (raw === null) return []
  const data: unknown = JSON.parse(raw)
  if (!Array.isArray(data) || data.some(id => typeof id !== 'string' || !id) || new Set(data).size !== data.length) throw Error('Invalid unread markers')
  return data as string[]
}
function storageFailed() {
  if (useSessionNotificationStore.getState().storageFailed) return
  useSessionNotificationStore.setState({storageFailed: true})
  useAppStore.getState().toast({title: {key: 'ui.notifications.storage'}, tone: 'warn'})
}
function mergePending(ids: string[]) {
  const next = new Set(ids)
  for (const [id, unread] of pendingUnread) {if (unread) next.add(id); else next.delete(id)}
  return [...next]
}
export function loadSessionUnread() {
  try {useSessionNotificationStore.setState({unread: mergePending(storedUnread())})} catch {storageFailed()}
}
function changeUnread(sessionId: string, unread: boolean) {
  pendingUnread.set(sessionId, unread)
  let ids = useSessionNotificationStore.getState().unread
  try {ids = storedUnread()} catch {storageFailed()}
  const next = mergePending(ids)
  useSessionNotificationStore.setState({unread: next})
  try {
    // 每次写入前核对原件，损坏时只保留内存意图。
    const current = mergePending(storedUnread())
    useSessionNotificationStore.setState({unread: current})
    localStorage.setItem(storageKey, JSON.stringify(current))
    pendingUnread.clear()
  } catch {storageFailed()}
}
export function markSessionRead(sessionId: string) {
  if (useSessionNotificationStore.getState().unread.includes(sessionId) || pendingUnread.has(sessionId)) changeUnread(sessionId, false)
  notifications.get(sessionId)?.close()
  notifications.delete(sessionId)
  void desktop()?.clearTaskNotification?.(sessionId).catch(() => undefined)
}
export function openNotifiedSession(sessionId: string) {
  const app = useAppStore.getState()
  if (!app.sessions.some(session => session.id === sessionId)) return
  app.setSettingsOpen(false)
  app.selectSession(sessionId)
  if (useAppStore.getState().inspectorExpanded) useAppStore.getState().toggleInspectorExpanded()
  useAppStore.getState().closeDrawer()
  markSessionRead(sessionId)
}
export function listenForTaskNotificationOpen() {
  return desktop()?.onTaskNotificationOpen?.(openNotifiedSession) ?? (() => undefined)
}
export function prepareTaskNotifications() {
  if (desktop()?.notifyTask || typeof Notification === 'undefined' || Notification.permission !== 'default') return
  void Notification.requestPermission().catch(() => undefined)
}
export function notifySessionCompletion(row: SessionCompletion) {
  const app = useAppStore.getState()
  const title = {key: 'ui.notifications.' + row.status}
  const session = app.sessions.find(session => session.id === row.session_id)
  const description = session?.title?.trim() || sessionTaskTitle(session?.summary) || {key: 'ui.components.sidebar.ProjectSidebar.a91ae6035092'}
  changeUnread(row.session_id, true)
  app.toast({title, description, tone: row.status === 'failed' ? 'danger' : 'info',
    action: {label: {key: 'ui.notifications.open'}, run: () => openNotifiedSession(row.session_id)}})
  const payload = {sessionId: row.session_id, title: t(title.key), body: noticeText(description)}
  if (desktop()?.notifyTask) {
    void desktop()!.notifyTask!(payload).catch(() => undefined)
  } else if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
    try {
      notifications.get(row.session_id)?.close()
      const notification = new Notification(payload.title, {body: payload.body, tag: 'harness-task-' + row.session_id})
      notifications.set(row.session_id, notification)
      notification.onclick = () => {
        if (notifications.get(row.session_id) !== notification) return
        window.focus(); openNotifiedSession(row.session_id); notification.close()
      }
      notification.onclose = () => {if (notifications.get(row.session_id) === notification) notifications.delete(row.session_id)}
    } catch { /* 浏览器不支持系统通知时，页面通知和未读标记仍可使用。 */ }
  }
}
export const sessionUnreadStorageKey = storageKey
