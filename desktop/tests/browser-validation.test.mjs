import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {test} from 'node:test'
import {NativeBrowserError} from '../browser-errors.mjs'
import {websiteURL, viewBounds} from '../browser-policy.mjs'
import {PageFind} from '../find.mjs'
import {PageReadiness} from '../page-readiness.mjs'
import {beginNativeInput} from '../native-input.mjs'
import {NativeViewports} from '../native-viewport.mjs'

const resource = key => error => error instanceof NativeBrowserError && error.copy.key === 'browser.desktop.' + key

test('website URL and layout validation keep explicit errors before accepting input',()=>{
  for(const url of ['not a URL','file:///tmp/private','https://user:secret@fixture.invalid'])assert.throws(()=>websiteURL(url),resource('invalidAddress'))
  assert.throws(()=>websiteURL('not a URL'),error=>error.cause instanceof TypeError)
  assert.equal(websiteURL('https://fixture.invalid/原文'),'https://fixture.invalid/%E5%8E%9F%E6%96%87')
  for(const bounds of [null,{}, {x:0,y:0,width:'1',height:1}, {x:0,y:0,width:Infinity,height:1}])assert.throws(()=>viewBounds(bounds,[100,100]),resource('boundsInvalid'))
  assert.deepEqual(viewBounds({x:-1,y:20,width:200,height:200},[100,100]),{x:0,y:20,width:100,height:80})
})

test('find rejects invalid text without calling Chromium or changing state',()=>{
  const wc=new EventEmitter(),calls=[]
  wc.findInPage=(...args)=>{calls.push(args);return calls.length}
  wc.stopFindInPage=action=>calls.push(action);wc.isDestroyed=()=>false
  const find=new PageFind(wc)
  for(const value of [true,null,[], 'x'.repeat(1001)]){
    assert.throws(()=>find.search(value),error=>resource('findInvalid')(error)&&error.copy.params.count===1000)
    assert.deepEqual(find.state,{open:false,text:'',matches:0,ordinal:0})
    assert.deepEqual(calls,[])
  }
  find.search('Original 原文')
  wc.emit('found-in-page',{}, {requestId:99,matches:8,activeMatchOrdinal:1})
  assert.equal(find.state.matches,0)
  wc.emit('found-in-page',{}, {requestId:1,matches:2,activeMatchOrdinal:1})
  assert.equal(find.state.matches,2)
  find.close();assert.equal(calls.at(-1),'clearSelection')
})

test('page readiness errors keep the retry code and release their timeout listener',async()=>{
  const ready=new PageReadiness()
  await assert.rejects(ready.waitSelected(()=> 'tab',()=>false),resource('pageFailed'))
  ready.begin('tab')
  await assert.rejects(ready.waitSelected(()=> 'tab',()=>false,0),error=>resource('pageInitializing')(error)&&error.code==='browser/desktop-page')
  await assert.rejects(ready.waitSelected(()=> 'tab',()=>false,1),resource('pageInitializing'))
  assert.equal(ready.listenerCount('changed'),0)
  let complete=false
  const waiting=ready.waitSelected(()=> 'tab',()=>complete)
  complete=true;ready.finish('tab');await waiting
  assert.equal(ready.listenerCount('changed'),0)
})

test('input ownership changes reject before recording automation',async()=>{
  const automation=new Set(),state={owner:'agent'}
  await assert.rejects(beginNativeInput(automation,'owner',async()=>state,()=>false),resource('inputChanged'))
  assert.equal(automation.size,0)
  assert.equal(await beginNativeInput(automation,'owner',async()=>state),state)
  assert.deepEqual([...automation],['owner'])
})

test('viewport errors preserve initialization, validity, timeout and stale measurement distinctions',async()=>{
  let read=async()=>({width:800,height:600}),bounds={x:0,y:0,width:800,height:600}
  const view={getBounds:()=>bounds,webContents:{id:1,isDestroyed:()=>false,getZoomFactor:()=>1,executeJavaScriptInIsolatedWorld:()=>read()}}
  for(const [value,key] of [[{width:0,height:1},'viewportPending'],[{width:true,height:1},'viewportInvalid'],[{width:40000,height:1},'viewportInvalid']]){
    read=async()=>value
    await assert.rejects(new NativeViewports().get(view),resource(key))
  }
  read=()=>new Promise(()=>{})
  await assert.rejects(new NativeViewports({timeout:1}).get(view),resource('viewportTimeout'))
  let finish
  read=()=>new Promise(resolve=>{finish=resolve})
  const viewports=new NativeViewports(),waiting=viewports.get(view)
  bounds={...bounds,width:900};finish({width:800,height:600})
  await assert.rejects(waiting,resource('viewportChanged'))
  assert.equal(viewports.peek(view),null)
  read=async()=>({width:900,height:600})
  assert.deepEqual(await viewports.get(view),{width:900,height:600})
})
