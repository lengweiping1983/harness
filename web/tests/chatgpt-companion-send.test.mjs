import assert from 'node:assert/strict'
import test from 'node:test'
import {sendOnce} from '../../extensions/chatgpt-companion/page.mjs'

const url = 'https://chatgpt.com/c/00000000-0000-0000-0000-000000000001'

function fixture(t, appended) {
  let now = 0, clicks = 0
  t.mock.method(Date, 'now', () => now)
  t.mock.method(globalThis, 'setTimeout', (callback, milliseconds) => {now += milliseconds; queueMicrotask(callback); return 0})
  const originalInputEvent = globalThis.InputEvent
  globalThis.InputEvent = class {}
  t.after(() => {if (originalInputEvent) globalThis.InputEvent = originalInputEvent; else delete globalThis.InputEvent})
  const messages = []
  const message = (text, heading = '') => ({
    textContent: heading + text, contains: () => false,
    compareDocumentPosition(other) {return messages.indexOf(this) < messages.indexOf(other) ? 4 : 2},
    cloneNode() {
      const body = {textContent: heading + text, querySelectorAll: () => heading ? [{remove() {body.textContent = text}}] : []}
      return body
    },
  })
  messages.push(message('继续'))
  const editor = {value: '', focus() {}, dispatchEvent() {}, closest: () => form}
  const button = {disabled: false, click() {clicks++; if (appended !== null) messages.push(message(appended, 'You said')); editor.value = ''}}
  const form = {querySelector: () => null, querySelectorAll: selector => selector.startsWith('button[') ? [button] : []}
  const doc = {querySelectorAll: selector => {
    if (selector.startsWith('#prompt-textarea')) return [editor]
    if (selector === '[data-message-author-role="user"]') return messages
    return []
  }}
  return {doc, editor, clicks: () => clicks}
}

test('only a newly appended exact continuation confirms the click, excluding the role heading', async t => {
  const page = fixture(t, '继续')
  assert.deepEqual(await sendOnce(page.doc, url, () => url, '继续', () => true), {outcome: 'confirmed'})
  assert.equal(page.clicks(), 1)
})

test('an old identical continuation does not confirm a click with no new user message', async t => {
  const page = fixture(t, null)
  assert.deepEqual(await sendOnce(page.doc, url, () => url, '继续', () => true), {outcome: 'unknown', reason: 'send_unconfirmed'})
  assert.equal(page.clicks(), 1)
})

test('a new message merely containing the continuation does not confirm it', async t => {
  const page = fixture(t, '请继续开发')
  assert.deepEqual(await sendOnce(page.doc, url, () => url, '继续', () => true), {outcome: 'unknown', reason: 'send_unconfirmed'})
  assert.equal(page.clicks(), 1)
})
