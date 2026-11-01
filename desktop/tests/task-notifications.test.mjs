import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {test} from 'node:test'
import {TaskNotifications} from '../task-notifications.mjs'

function fixture() {
  const created = [], calls = []
  class Notification extends EventEmitter {
    static isSupported() {return true}
    constructor(options) {super(); this.options = options; created.push(this)}
    show() {calls.push('show')}
    close() {calls.push('close'); this.emit('close')}
  }
  const window = {isDestroyed: () => false, isMinimized: () => true, restore: () => calls.push('restore'),
    show: () => calls.push('window-show'), focus: () => calls.push('focus'),
    webContents: {send: (...args) => calls.push(args)}}
  return {created, calls, window, Notification, manager: new TaskNotifications({Notification, getWindow: () => window})}
}
test('notification click restores the original window and opens its conversation once', () => {
  const {manager, created, calls} = fixture()
  assert.equal(manager.show({sessionId: 'background', title: '任务已完成', body: 'Original 原文'}), true)
  assert.deepEqual(created[0].options, {title: '任务已完成', body: 'Original 原文'})
  created[0].emit('click'); created[0].emit('click')
  assert.deepEqual(calls, ['show', 'restore', 'window-show', 'focus', ['task-notification-open', 'background'], 'close'])
  assert.equal(manager.notifications.size, 0)
})
test('read, replacement, unsupported platforms, destroyed targets and failure preserve ownership', () => {
  const {manager, created, calls, window, Notification} = fixture()
  const payload = {sessionId: 'original', title: 'Task completed', body: 'Original'}
  manager.show(payload); manager.show(payload)
  created[0].emit('click')
  assert.equal(calls.includes('restore'), false)
  manager.clear('original'); assert.equal(manager.notifications.size, 0)
  Notification.isSupported = () => false
  assert.equal(manager.show(payload), false)
  Notification.isSupported = () => true
  manager.show(payload); window.isDestroyed = () => true
  created.at(-1).emit('click'); assert.equal(calls.includes('restore'), false)
  manager.close(); assert.equal(manager.notifications.size, 0)
  for (const bad of [null, {}, {...payload, sessionId: []}, {...payload, title: ''}]) assert.throws(() => manager.show(bad))
})
