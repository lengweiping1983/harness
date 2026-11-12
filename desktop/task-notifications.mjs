/** 只持有当前主窗口的任务通知；点击时恢复窗口并路由到原对话。 */
export class TaskNotifications {
  constructor({Notification, getWindow}) {
    this.Notification = Notification
    this.getWindow = getWindow
    this.notifications = new Map()
  }
  show(payload) {
    if (!payload || ['sessionId', 'title', 'body'].some(key => typeof payload[key] !== 'string' || !payload[key].trim()) ||
        payload.sessionId.length > 256 || payload.title.length > 512 || payload.body.length > 4096) throw Error('Invalid task notification')
    const window = this.getWindow()
    if (!window || window.isDestroyed() || !this.Notification.isSupported()) return false
    this.clear(payload.sessionId)
    const notification = new this.Notification({title: payload.title, body: payload.body})
    this.notifications.set(payload.sessionId, notification)
    const remove = () => {if (this.notifications.get(payload.sessionId) === notification) this.notifications.delete(payload.sessionId)}
    notification.on('close', remove)
    notification.on('failed', remove)
    notification.on('click', () => {
      if (this.notifications.get(payload.sessionId) !== notification || this.getWindow() !== window || window.isDestroyed()) return
      if (window.isMinimized()) window.restore()
      window.show()
      window.focus()
      window.webContents.send('task-notification-open', payload.sessionId)
      this.clear(payload.sessionId)
    })
    try {notification.show(); return true} catch {this.clear(payload.sessionId); return false}
  }
  clear(sessionId) {
    const notification = this.notifications.get(sessionId)
    this.notifications.delete(sessionId)
    notification?.close()
  }
  close() {
    for (const sessionId of this.notifications.keys()) this.clear(sessionId)
  }
}
