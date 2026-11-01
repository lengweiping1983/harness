import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {readFileSync} from 'node:fs'
import {runInNewContext} from 'node:vm'
import {test} from 'node:test'

test('preload forwards scoped browser errors unchanged and releases its listener',async()=>{
 const ipcRenderer=new EventEmitter()
 let desktop
 const electron={ipcRenderer,contextBridge:{exposeInMainWorld(name,value){assert.equal(name,'harnessDesktop');desktop=value}}}
 runInNewContext(readFileSync(new URL('../preload.cjs',import.meta.url),'utf8'),{require(name){assert.equal(name,'electron');return electron}})
 const seen=[]
 const unsubscribe=desktop.onBrowserError(value=>seen.push(value))
 const failure={session_id:'Original-owner',message:'Original-{name} 原文'}
 ipcRenderer.emit('browser-error',{},failure)
 assert.equal(seen.length,1)
 assert.equal(seen[0],failure)
 unsubscribe()
 assert.equal(ipcRenderer.listenerCount('browser-error'),0)
 ipcRenderer.emit('browser-error',{},failure)
 assert.equal(seen.length,1)
 const result={failure:{code:'browser/dialog-inactive',copy:{key:'browser.desktop.dialogInactive',params:{}}}}
 ipcRenderer.invoke=async(channel,action,args)=>{assert.equal(channel,'browser');assert.equal(action,'dialog_response');assert.deepEqual(args,{session_id:'owner',id:'Original'});return result}
 assert.equal(await desktop.browser('dialog_response',{session_id:'owner',id:'Original'}),result)
})
