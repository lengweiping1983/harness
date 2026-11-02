import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {readFileSync} from 'node:fs'
import {runInNewContext} from 'node:vm'
import {test} from 'node:test'

// 隔离 preload 只能看到 Electron 显式开放的受限全局：受限 require、只读 platform 与受限
// window/document，宿主 process、console 等一律不可见。沙箱漏声明全局时 Node 只给出不透明的
// `ReferenceError: X is not defined`，容易被误读成产品缺陷；这里改写成指向本夹具的明确失败。
function runPreload({platform,ipcRenderer,events,dataset}) {
 let desktop
 const electron={ipcRenderer,contextBridge:{exposeInMainWorld(name,value){assert.equal(name,'harnessDesktop');desktop=value}}}
 try {
  runInNewContext(readFileSync(new URL('../preload.cjs',import.meta.url),'utf8'),{
   require(name){assert.equal(name,'electron');return electron},process:{platform},
   window:{addEventListener(name,handler,options){assert.equal(options.once,true);events.set(name,handler)}},
   document:{documentElement:{dataset}},
  })
 } catch (error) {
  if (error?.name!=='ReferenceError') throw error
  throw new Error(`preload 沙箱缺少 Electron 隔离 preload 的声明全局：${error.message}；请补齐本夹具的沙箱声明，不要注入宿主全局`,{cause:error})
 }
 return desktop
}

for (const platform of ['darwin','win32','linux']) test(platform+': preload keeps platform chrome and scoped browser error ownership',async()=>{
 const ipcRenderer=new EventEmitter()
 const events=new Map(), dataset={}
 const desktop=runPreload({platform,ipcRenderer,events,dataset})
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
