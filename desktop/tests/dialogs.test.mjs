import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {test} from 'node:test'
import {NativeDialogs} from '../dialogs.mjs'
import {NativeBrowserError} from '../browser-errors.mjs'

test('supported adapter routes answers and cancellation to the owning tab', async () => {
  const contents = new EventEmitter()
  contents.isDestroyed = () => false
  const native = () => assert.fail('default handler should be replaced')
  contents.on('-run-dialog', native)
  const dialogs = new NativeDialogs()
  assert.equal(dialogs.watch('first', contents), true)
  assert.equal(contents.listenerCount('-run-dialog'), 1)
  const answers = []
  contents.emit('-run-dialog', {dialogType:'prompt', messageText:'Name?', frame:{url:'https://example.test'}, defaultPromptText:'hello'}, (...args) => answers.push(args))
  const entry = dialogs.snapshot('first')
  assert.equal(entry.default_value, 'hello')
  assert.equal(dialogs.snapshot('other'), null)
  await dialogs.respond('first', contents, {id:entry.id, accept:true, prompt_text:'world'})
  assert.deepEqual(answers, [[true, 'world']])
  assert.equal(dialogs.snapshot('first'), null)
  dialogs.open('other', 'alert', 'Other', '', '', () => {})
  contents.emit('-run-dialog', {dialogType:'confirm', messageText:'Continue?', frame:{url:'https://example.test'}}, () => {})
  contents.emit('-cancel-dialogs')
  assert.equal(dialogs.snapshot('first'), null)
  assert.equal(dialogs.snapshot('other').message, 'Other')
})

for (const count of [0, 2]) test(`unsupported adapter with ${count} listeners preserves native handlers`, () => {
  const contents = new EventEmitter()
  const calls = []
  for (let index = 0; index < count; index++) contents.on('-run-dialog', () => calls.push(index))
  const before = contents.rawListeners('-run-dialog')
  const warnings = []
  const dialogs = new NativeDialogs(() => {}, message => warnings.push(message))
  assert.equal(dialogs.watch('first', contents), false)
  assert.deepEqual(contents.rawListeners('-run-dialog'), before)
  assert.equal(contents.listenerCount('-cancel-dialogs'), 0)
  contents.emit('-run-dialog')
  assert.equal(calls.length, count)
  assert.equal(warnings.length, 1)
  assert.equal(dialogs.snapshot('first'), null)
})

test('rejected responses preserve the pending dialog', async () => {
  const dialogs = new NativeDialogs()
  const answers = []
  dialogs.open('first', 'confirm', 'Continue?', '', '', answer => answers.push(answer))
  const entry = dialogs.snapshot('first')
  const contents = {isDestroyed:() => false}
  const inactive = error => error instanceof NativeBrowserError && error.code === 'browser/dialog-inactive' && error.copy.key === 'browser.desktop.dialogInactive'
  await assert.rejects(dialogs.respond('other', contents, {id:entry.id, accept:true}), inactive)
  await assert.rejects(dialogs.respond('first', contents, {id:'stale', accept:true}), inactive)
  assert.deepEqual(answers, [])
  assert.equal(dialogs.snapshot('first').id, entry.id)
})

test('invalid answers and unconfirmed responses retain their resource identity without replay', async () => {
  const dialogs = new NativeDialogs()
  const contents = {isDestroyed:() => false}
  const original = Error('External original {name} 原文')
  let answers = 0
  dialogs.open('first', 'prompt', 'Original', '', '', () => {answers++; throw original})
  const entry = dialogs.snapshot('first')
  for (const args of [{accept:'yes'}, {accept:true}, {accept:true,prompt_text:42}]) {
    await assert.rejects(dialogs.respond('first', contents, {id:entry.id,...args}), error => error.code === 'browser/dialog-invalid' && error.copy.key === 'browser.desktop.dialogInvalid')
    assert.equal(answers, 0)
  }
  await assert.rejects(dialogs.respond('first', contents, {id:entry.id,accept:true,prompt_text:'Original'}), error => error === original)
  assert.equal(dialogs.snapshot('first').response_unconfirmed, true)
  for (let attempt = 0; attempt < 2; attempt++) {
    await assert.rejects(dialogs.respond('first', contents, {id:entry.id,accept:true,prompt_text:'Original'}), error => error.code === 'browser/dialog-pending' && error.copy.key === 'browser.desktop.dialogPending')
    assert.equal(answers, 1)
  }
})
