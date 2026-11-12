import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {readFileSync} from 'node:fs'
import {test} from 'node:test'
import {createBrowserRuntime} from '../browser-runtime.mjs'

function fixture() {
  const views=[], menus=[], publications=[], handlers=new Map()
  let labels=null, load=async()=>{}, target=async id=>({targetInfo:{targetId:'target-'+id}})
  class View {
    constructor() {
      const wc=this.webContents=new EventEmitter()
      wc.id=views.length+1
      let destroyed=false,url='about:blank'
      Object.assign(wc,{
        isDestroyed:()=>destroyed,getURL:()=>url,getTitle:()=>'',isLoading:()=>false,getZoomFactor:()=>1,
        navigationHistory:{canGoBack:()=>false,canGoForward:()=>false},setWindowOpenHandler(){},focus(){},
        debugger:{attach(){},detach(){},isAttached:()=>true,sendCommand:()=>target(wc.id)},
        loadURL:async destination=>{await load(destination);url=destination},
        close:()=>{destroyed=true;wc.emit('destroyed')},
      })
      wc.on('-run-dialog',()=>{})
      views.push(this)
    }
    getBounds(){return {x:0,y:0,width:800,height:600}}
    setBounds(){}
    setVisible(){}
  }
  const window=new EventEmitter()
  window.webContents=new EventEmitter()
  Object.assign(window.webContents,{mainFrame:{url:'http://localhost:1234'},send:(...args)=>publications.push(args),focus(){}})
  Object.assign(window,{isDestroyed:()=>false,getContentSize:()=>[1200,800],contentView:{addChildView(){},removeChildView(){}}})
  const ipcMain=new EventEmitter();ipcMain.handle=(name,handler)=>handlers.set(name,handler)
  const session=new EventEmitter();session.setPermissionCheckHandler=()=>{};session.setPermissionRequestHandler=()=>{}
  const runtime=createBrowserRuntime({app:{getLocale:()=>{throw Error('System language must not select website menu copy')}},WebContentsView:View,ipcMain,session:{fromPartition:()=>session},Menu:{buildFromTemplate:items=>({popup:()=>menus.push(items)})},clipboard:{writeText(){}},getWindow:()=>window,getDownloads:()=>undefined,getOrigin:()=> 'http://localhost:1234',getMenuText:()=>labels})
  runtime.attach()
  const action=(action,args={})=>handlers.get('browser')({sender:window.webContents,senderFrame:window.webContents.mainFrame},action,args)
  return {views,menus,publications,action,shortcut:input=>window.webContents.emit('before-input-event',{preventDefault(){}},input),bridge:args=>runtime.bridgeAction(args),close:()=>runtime.close(),setLabels:value=>{labels=value},setLoad:value=>{load=value},setTarget:value=>{target=value}}
}

test('website menus use current interface resources and reject actions from a previous conversation',async()=>{
  const app=fixture()
  try{
    await app.action('bind',{session_id:'first'})
    await app.action('new_tab',{url:'https://fixture.invalid'})
    await app.action('layout',{visible:true,bounds:{x:0,y:0,width:800,height:600}})
    const params={linkURL:'https://fixture.invalid/link',isEditable:true,editFlags:{canUndo:true,canRedo:false,canCut:true,canCopy:true,canPaste:true},x:1,y:2}
    const wc=app.views[0].webContents
    wc.emit('context-menu',{},params)
    assert.equal(app.menus.length,0)
    for(const locale of ['en','zh-CN','en']){
      const resources=JSON.parse(readFileSync(new URL('../../harness/i18n/locales/'+locale+'.json',import.meta.url)))
      const labels=Object.fromEntries(Object.entries(resources).filter(([key])=>key.startsWith('ui.desktop.menu.')).map(([key,value])=>[key.slice('ui.desktop.menu.'.length),value]))
      app.setLabels(labels);wc.emit('context-menu',{},params)
      const items=app.menus.at(-1).filter(item=>item.id)
      const keys=['contextOpenLink','contextCopyLink','undo','redo','cut','copy','paste','selectAll','contextInspect']
      assert.deepEqual(items.map(item=>item.label),keys.map(key=>labels[key]))
      assert.equal(items.find(item=>item.id==='redo').enabled,false)
    }
    const oldOpen=app.menus.at(-1).find(item=>item.id==='open-link').click
    await app.action('bind',{session_id:'second'})
    oldOpen()
    await new Promise(setImmediate)
    assert.equal(app.views.length,1)
  }finally{app.close()}
})

for (const scope of ['conversation','runtime']) for (const phase of ['load','target']) for (const outcome of ['reject','resolve']) test(`closing the ${scope} browser invalidates a pending ${phase} that later ${outcome}s`,async()=>{
  const app=fixture()
  try{
    await app.action('bind',{session_id:'first'})
    const first=await app.action('new_tab',{url:'https://fixture.invalid/Original'})
    await app.action('close_tab',{id:first.active_tab})
    let finish,started
    const began=new Promise(resolve=>{started=resolve})
    const original=Error('Original late loading failure 原文')
    const pending=()=>{started();return new Promise((resolve,reject)=>{finish=()=>outcome==='reject'?reject(original):resolve({targetInfo:{targetId:'late'}})})}
    if(phase==='load')app.setLoad(pending);else app.setTarget(pending)
    const reopening=app.action('reopen_tab')
    const observed=outcome==='reject'?assert.rejects(reopening,error=>error===original):reopening
    await began
    if(scope==='conversation')await app.bridge({action:'close_session',session_id:'first'});else app.close()
    finish()
    const result=await observed
    if(outcome==='resolve')assert.equal(result.failure?.code,'browser/tab-closed')
    const state=await app.action('bind',{session_id:'first'})
    assert.deepEqual(state.tabs,[])
    assert.equal(state.can_reopen,false)
  }finally{app.close()}
})

test('tab limits use a counted resource and do not create an extra view',async()=>{
  const app=fixture()
  try{
    await app.action('bind',{session_id:'first'})
    for(let index=0;index<12;index++)await app.action('new_tab')
    assert.deepEqual(await app.action('new_tab'),{failure:{code:'browser/tab-limit',copy:{key:'browser.desktop.tabLimit',params:{count:12}}}})
    assert.equal(app.views.length,12)
    assert.equal((await app.action('reopen_tab')).failure.code,'browser/tab-limit')
    await app.action('layout',{visible:true,bounds:{x:0,y:0,width:800,height:600}})
    await app.action('keyboard_context',{active:true})
    app.shortcut({type:'keyDown',key:'t',meta:process.platform==='darwin',control:process.platform!=='darwin'})
    await new Promise(setImmediate)
    assert.deepEqual(app.publications.filter(([kind])=>kind==='browser-error').at(-1),['browser-error',{session_id:'first',message:'browser/tab-limit',code:'browser/tab-limit',copy:{key:'browser.desktop.tabLimit',params:{count:12}}}])
  }finally{app.close()}
})

test('a failed old reopen cannot add history to a newly opened browser with the same conversation id',async()=>{
  const app=fixture()
  try{
    await app.action('bind',{session_id:'first'})
    const first=await app.action('new_tab',{url:'https://fixture.invalid/Old'})
    await app.action('close_tab',{id:first.active_tab})
    let reject,started
    const began=new Promise(resolve=>{started=resolve})
    app.setLoad(()=>{started();return new Promise((_resolve,rejectLoad)=>{reject=rejectLoad})})
    const original=Error('Original old browser failure')
    const rejected=assert.rejects(app.action('reopen_tab'),error=>error===original)
    await began
    await app.bridge({action:'close_session',session_id:'first'})
    app.setLoad(async()=>{})
    const fresh=await app.action('new_tab',{url:'https://fixture.invalid/New'})
    await app.action('close_tab',{id:fresh.active_tab})
    reject(original);await rejected
    const restored=await app.action('reopen_tab')
    assert.equal(restored.tabs[0].url,'https://fixture.invalid/New')
    assert.equal(restored.can_reopen,false)
  }finally{app.close()}
})

test('reload recovery preserves a rejected load and keeps the failed tab visible',async()=>{
  const app=fixture()
  try{
    await app.action('bind',{session_id:'first'})
    await app.action('new_tab',{url:'https://fixture.invalid/Original'})
    app.views[0].webContents.emit('did-fail-load',{},-105,'Original DNS failure','https://fixture.invalid/Original',true)
    const original=Error('Original retry failure 原文')
    let loads=0
    app.setLoad(async()=>{loads++;throw original})
    await assert.rejects(app.action('reload'),error=>error===original)
    const state=await app.action('bind',{session_id:'first'})
    assert.equal(state.tabs[0].issue,'load-failed')
    assert.equal(state.tabs[0].url,'https://fixture.invalid/Original')
    assert.equal(loads,1)
    app.setLoad(async()=>{loads++})
    const recovered=await app.action('reload')
    assert.equal(recovered.tabs[0].issue,null)
    assert.equal(loads,2)
  }finally{app.close()}
})

test('native IPC validation failures preserve identity and do not create views',async()=>{
  const app=fixture()
  try{
    await app.action('bind',{session_id:'first'})
    for(const [action,args,key] of [
      ['bind',{session_id:'../bad'},'invalidSession'],
      ['control',{owner:'invalid'},'invalidOwner'],
      ['select_tab',{id:'stale'},'tabMissing'],
      ['close_tab',{id:'stale'},'tabMissing'],
      ['layout',{bounds:null},'boundsInvalid'],
      ['new_tab',{url:'file:///tmp/private'},'invalidAddress'],
      ['unknown',{},'actionInvalid'],
    ]){
      const result=await app.action(action,args)
      assert.equal(result.failure.copy.key,'browser.desktop.'+key)
      assert.equal(app.views.length,0)
    }
    const state=await app.action('new_tab',{url:'https://fixture.invalid'})
    assert.equal((await app.action('zoom',{direction:2})).failure.copy.key,'browser.desktop.zoomInvalid')
    assert.equal((await app.action('find',{text:true})).failure.copy.key,'browser.desktop.findInvalid')
    assert.equal(state.session_id,'first')
  }finally{app.close()}
})

test('an explicitly aborted recovery load keeps cancellation semantics without retrying',async()=>{
  const app=fixture()
  try{
    await app.action('bind',{session_id:'first'})
    await app.action('new_tab',{url:'https://fixture.invalid/Original'})
    app.views[0].webContents.emit('did-fail-load',{},-105,'Original failure','https://fixture.invalid/Original',true)
    let loads=0
    app.setLoad(async()=>{loads++;throw Object.assign(Error('Cancelled'),{code:'ERR_ABORTED'})})
    const state=await app.action('reload')
    assert.equal(state.tabs[0].url,'https://fixture.invalid/Original')
    assert.equal(state.tabs[0].issue,null)
    assert.equal(loads,1)
  }finally{app.close()}
})

test('a failed reopen restores history to its original conversation after switching',async()=>{
  const app=fixture()
  try{
    await app.action('bind',{session_id:'first'})
    const first=await app.action('new_tab',{url:'https://fixture.invalid/Original'})
    await app.action('close_tab',{id:first.active_tab})
    let reject,started
    const began=new Promise(resolve=>{started=resolve})
    app.setLoad(()=>{started();return new Promise((_resolve,rejectLoad)=>{reject=rejectLoad})})
    const reopening=app.action('reopen_tab')
    const original=Error('Original navigation failure 原文')
    const rejected=assert.rejects(reopening,error=>error===original)
    await began
    await app.action('bind',{session_id:'second'})
    reject(original);await rejected
    assert.equal((await app.action('bind',{session_id:'second'})).can_reopen,false)
    assert.equal((await app.action('bind',{session_id:'first'})).can_reopen,true)
    app.setLoad(async()=>{})
    const restored=await app.action('reopen_tab')
    assert.equal(restored.tabs.length,1)
    assert.equal(restored.tabs[0].url,'https://fixture.invalid/Original')
    assert.equal(restored.can_reopen,false)
    assert.equal((await app.action('bind',{session_id:'second'})).tabs.length,0)
  }finally{app.close()}
})
