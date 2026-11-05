import assert from 'node:assert/strict'
import {test} from 'node:test'
import {mkdtemp, readFile, rm, writeFile} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {DesktopVoice, voiceReply} from '../voice.mjs'

test('Windows dictation checks CPU before restoring saved shortcuts and preserves unsupported preferences',async t => {
  const directory=await mkdtemp(path.join(tmpdir(),'harness-voice-cpu-'))
  t.after(() => rm(directory,{recursive:true,force:true}))
  const original=Buffer.from(JSON.stringify({version:1,enabled:true,shortcut:'Control+Alt+Space'})+'\n')
  const file=path.join(directory,'voice.json')
  await writeFile(file,original)
  for (const supported of [false,true]) {
    const registered=[],removed=[]
    const fetchCapabilities=t.mock.fn(async () => Response.json({cpu_supported:supported,
      reason:supported ? null : {key:'ui.voice.global.cpuUnsupported'}}))
    const voice=new DesktopVoice({app:{getPath:() => directory},platform:'win32',getURL:() => 'http://127.0.0.1:8765',fetchCapabilities,
      globalShortcut:{register:key => {registered.push(key); return true},unregister:key => removed.push(key)}})
    await voice.initialize()
    assert.equal(voice.snapshot().supported,supported)
    assert.equal(voice.snapshot().enabled,supported)
    assert.deepEqual(registered,supported ? ['Control+Alt+Space'] : [])
    assert.equal(fetchCapabilities.mock.calls[0].arguments[0].href,'http://127.0.0.1:8765/api/v1/voice/dictation')
    if (!supported) {
      const native=t.mock.method(voice,'native',async () => {throw Error('must not capture')})
      await voice.toggle()
      assert.equal(native.mock.callCount(),0)
      assert.deepEqual(await voiceReply(() => voice.configure({enabled:true,shortcut:'Control+Shift+Space'})),
        {ok:false,copy:{key:'ui.voice.global.cpuUnsupported'}})
    }
    assert.deepEqual(await readFile(file),original)
    await voice.close()
    assert.deepEqual(removed,supported ? ['Control+Alt+Space'] : [])
  }
})

test('Windows dictation remains disabled when CPU capability cannot be confirmed',async t => {
  t.mock.method(console,'error',() => {})
  for (const response of [Response.json({}),Response.json({cpu_supported:'true'}),new Response('',{status:503})]) {
    const register=t.mock.fn()
    const voice=new DesktopVoice({app:{getPath:() => '/fixture'},platform:'win32',getURL:() => 'http://127.0.0.1:8765',
      fetchCapabilities:async () => response,globalShortcut:{register}})
    await voice.initialize()
    assert.equal(voice.snapshot().supported,false)
    assert.deepEqual(voice.snapshot().error,{key:'ui.voice.global.cpuCheckFailed'})
    assert.equal(register.mock.callCount(),0)
    await voice.close()
  }
})
