import assert from 'node:assert/strict'
import {test} from 'node:test'
import {ScreenshotSession} from '../screenshot.mjs'

test('screenshot cleanup attempts every owned release after restoration failure',()=>{
 for(const stage of ['image','fullscreen','top','bounds','show','focus','resume']){
  const attempts=[]
  const failure=new Error('Original '+stage+' failure')
  const act=name=>()=>{attempts.push(name);if(stage===name)throw failure}
  const window={isDestroyed:()=>false,setSimpleFullScreen:act('fullscreen'),setFullScreen:act('fullscreen'),setAlwaysOnTop:act('top'),setBounds:act('bounds'),show:act('show'),focus:act('focus')}
  const session=new ScreenshotSession({window,releaseImage:act('image'),resume:act('resume')})
  session.active=true;session.snapshot={fullscreen:false,simpleFullscreen:false,maximized:false,bounds:{},normalBounds:{},alwaysOnTop:false}
  assert.throws(()=>session.finish(),error=>error===failure)
  assert.deepEqual(attempts,['image','fullscreen','top','bounds','show','focus','resume'])
  assert.equal(session.active,false)
  const count=attempts.length;assert.throws(()=>session.finish(),error=>error===failure);assert.equal(attempts.length,count)
 }
})

test('screenshot cleanup preserves all simultaneous original failures',()=>{
 const first=new Error('Original image failure'),second=new Error('Original resume failure')
 const session=new ScreenshotSession({window:{isDestroyed:()=>true},releaseImage:()=>{throw first},resume:()=>{throw second}})
 session.active=true
 assert.throws(()=>session.finish(),error=>error instanceof AggregateError&&error.errors[0]===first&&error.errors[1]===second)
})

test('failed capture retains its original error when restoration also fails',async()=>{
 for(const cleanupFails of [false,true]){
  const primary=new Error('Original capture {diagnostic}'),cleanup=new Error('Original window restoration {diagnostic}')
  const attempts=[]
  const window={getBounds:()=>({}),getNormalBounds:()=>({}),isMaximized:()=>false,isFullScreen:()=>false,isSimpleFullScreen:()=>false,isAlwaysOnTop:()=>false,isDestroyed:()=>false,
   setFullScreen:()=>{},setSimpleFullScreen:()=>{},setAlwaysOnTop:()=>{},setBounds:()=>{},show:()=>{attempts.push('show');if(cleanupFails)throw cleanup},focus:()=>attempts.push('focus')}
  const session=new ScreenshotSession({window,screen:{getDisplayMatching:()=>({size:{width:10,height:10},scaleFactor:1})},captureScreen:async()=>{throw primary},releaseImage:()=>attempts.push('image'),resume:()=>attempts.push('resume')})
  await assert.rejects(session.capture(),error=>cleanupFails?error instanceof AggregateError&&error.errors[0]===primary&&error.errors[1]===cleanup:error===primary)
  assert.deepEqual(attempts,['image','show','focus','resume'])
  assert.equal(session.active,false)
 }
})

test('unknown restoration blocks new capture without replaying window operations',async()=>{
 const failure=new Error('Original restoration unknown')
 const session=new ScreenshotSession({window:{isDestroyed:()=>true},releaseImage:()=>{throw failure},resume:()=>{}})
 session.active=true
 assert.throws(()=>session.finish(),error=>error===failure)
 await assert.rejects(session.capture(),error=>error===failure)
})
