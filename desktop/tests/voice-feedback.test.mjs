import assert from 'node:assert/strict'
import {test} from 'node:test'
import {DesktopVoice, voiceReply} from '../voice.mjs'

test('denied dictation input permission reaches the floating window and IPC as resource-only guidance', async t => {
  t.mock.method(console, 'error', () => {})
  const copy = {key:'ui.systemPermission.accessibility.settings'}, sent = []
  let targets = 0
  const voice = new DesktopVoice({app:{getPath:() => '/fixture'}, getWindow:() => ({isFocused:() => false}),
    systemPermissions:{request:async () => ({granted:false, copy})}})
  voice.registered = 'Control+Alt+Space'
  voice.native = async () => {targets++; return '123'}
  const window = {showInactive() {}, webContents:{send:(...args) => sent.push(args)}}
  voice.ensureWindow = () => {voice.window = window; return window}
  voice.loading = Promise.resolve()
  const reply = await voiceReply(async () => {
    try {await voice.toggle()} catch (error) {voice.fail(error); throw error}
  })
  await voice.loading
  assert.deepEqual(reply, {ok:false, copy})
  assert.deepEqual(voice.ready(), {action:'error', copy})
  assert.deepEqual(sent, [['voice-action', {action:'error', copy}]])
  assert.equal(targets, 0)
  assert.equal(voice.active, null)
})

test('dictation feedback preserves original external diagnostics and unwraps only resource errors', async () => {
  const copy = {key:'ui.voice.global.pasteUnconfirmed'}
  const original = new Error('Original native failure 原文')
  const nested = Object.assign(new Error('internal-resource', {cause:original}), {copy})
  const error = Object.assign(new Error('another-resource', {cause:nested}), {copy})
  assert.deepEqual(await voiceReply(async () => {throw error}), {ok:false, copy, diagnostic:original.message})
  const external = new Error('System input permission required')
  assert.deepEqual(await voiceReply(async () => {throw external}), {
    ok:false, copy:{key:'ui.voice.global.failed'}, diagnostic:external.message,
  })
})

test('invalid shortcut guidance does not expose nested resource names as diagnostics', async () => {
  const voice = new DesktopVoice({app:{getPath:() => '/fixture'}})
  assert.deepEqual(await voiceReply(() => voice.configure({enabled:true, shortcut:'invalid'})), {
    ok:false, copy:{key:'ui.voice.global.shortcutInvalid'},
  })
})
