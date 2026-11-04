import assert from 'node:assert/strict'
import {test} from 'node:test'
import {EventEmitter} from 'node:events'
import {desktopQuit} from '../quit.mjs'

const settle=()=>new Promise(resolve=>setImmediate(resolve))
test('install handoff waits for accepted window close and successful backend cleanup',async()=>{
  const app=new EventEmitter(),webContents=new EventEmitter(),calls=[]
  let destroyed=false,finish
  const window={webContents,isDestroyed:()=>destroyed,close:()=>calls.push('close')}
  app.quit=()=>calls.push('quit')
  desktopQuit({app,getWindow:()=>window,cleanup:()=>{calls.push('cleanup');return new Promise(resolve=>{finish=resolve})},beforeQuit:async({cleanupFailed})=>{assert.equal(cleanupFailed,false);calls.push('install')},onError:assert.fail})
  const event={preventDefault:()=>{}}
  app.emit('before-quit',event);await settle()
  assert.deepEqual(calls,['close'])
  destroyed=true;app.emit('before-quit',event);await settle()
  assert.deepEqual(calls,['close','cleanup'])
  app.emit('before-quit',event)
  finish();await settle()
  assert.deepEqual(calls,['close','cleanup','install','quit'])
})
test('cancelled leave never cleans up or launches installer; another quit can close again',async()=>{
  const app=new EventEmitter(),webContents=new EventEmitter();let closes=0
  const window={webContents,isDestroyed:()=>false,close:()=>{closes++}}
  const quit=desktopQuit({app,getWindow:()=>window,cleanup:assert.fail,beforeQuit:assert.fail,onError:assert.fail})
  quit.attach(window,()=>false)
  const event={preventDefault:()=>{}}
  app.emit('before-quit',event)
  webContents.emit('will-prevent-unload',event)
  await settle();assert.equal(closes,1)
  app.emit('before-quit',event);assert.equal(closes,2)
})
test('failed cleanup or handoff preserves the old application and reports failure before ordinary quit',async()=>{
  for(const stage of ['cleanup','handoff']) {
    const app=new EventEmitter(),original=Error(stage),errors=[];let installs=0,quits=0
    app.quit=()=>{quits++}
    desktopQuit({app,getWindow:()=>null,cleanup:async()=>{if(stage==='cleanup')throw original},beforeQuit:async({cleanupFailed})=>{
      if(cleanupFailed)throw Error('cleanup incomplete')
      installs++;throw original
    },onError:error=>errors.push(error)})
    app.emit('before-quit',{preventDefault:()=>{}});await settle()
    assert.equal(quits,1);assert.equal(installs,stage==='cleanup'?0:1);assert.ok(errors.includes(original))
  }
})
