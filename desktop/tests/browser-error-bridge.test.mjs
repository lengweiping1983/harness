import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {readFileSync} from 'node:fs'
import {runInNewContext} from 'node:vm'
import {test} from 'node:test'

for (const platform of ['darwin','win32','linux']) test(platform+': preload keeps platform chrome and scoped browser error ownership',async()=>{
 const ipcRenderer=new EventEmitter()
 let desktop
 const electron={ipcRenderer,contextBridge:{exposeInMainWorld(name,value){assert.equal(name,'harnessDesktop');desktop=value}}}
 const events=new Map(), dataset={}
 runInNewContext(readFileSync(new URL('../preload.cjs',import.meta.url),'utf8'),{
  require(name){assert.equal(name,'electron');return electron},process:{platform},
  window:{addEventListener(name,handler,options){assert.equal(options.once,true);events.set(name,handler)}},
  document:{documentElement:{dataset}},
 })
 assert.equal(desktop.platform,platform)
 assert.equal(dataset.desktopPlatform,undefined)
 if (platform==='linux') assert.equal(events.size,0)
 else {assert.equal(events.size,1);events.get('DOMContentLoaded')();assert.equal(dataset.desktopPlatform,platform)}
 if (platform==='win32') {
  const calls=[], request={key:'edit',x:8,y:28}, appearance={color:'#f0f2f3',symbolColor:'#20292c'}
  ipcRenderer.invoke=async(...args)=>calls.push(args)
  await desktop.popupMenu(request)
  await desktop.setTitleBarAppearance(appearance)
  assert.deepEqual(calls,[['desktop-menu-popup',request],['desktop-titlebar-appearance',appearance]])
 }
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
