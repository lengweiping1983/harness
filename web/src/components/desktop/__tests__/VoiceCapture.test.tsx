import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import type {ReactElement} from 'react'
import {VoiceCapture} from '../VoiceCapture'
import {setLocale,t} from '@/i18n'
import {ResourceError} from '@/lib/resourceError'

const hooks=vi.hoisted(()=>({cursor:0,slots:[] as unknown[],cleanups:[] as (()=>void)[]}))
const capture=vi.hoisted(()=>({create:vi.fn(),request:vi.fn(),prepare:vi.fn()}))
vi.mock('react',async original=>({...await original<typeof import('react')>(),
  useRef:(value:unknown)=>{const index=hooks.cursor++;return hooks.slots[index]??={current:value}},
  useState:(value:unknown)=>{const index=hooks.cursor++;if(!(index in hooks.slots))hooks.slots[index]=value
    return [hooks.slots[index],(next:unknown)=>{hooks.slots[index]=next}]},
  useEffect:(effect:()=>void|(()=>void),deps:unknown[])=>{const index=hooks.cursor++,old=hooks.slots[index] as unknown[]|undefined
    if(!old||deps.some((value,i)=>value!==old[i])){hooks.slots[index]=deps;const cleanup=effect();if(cleanup)hooks.cleanups.push(cleanup)}},
  useSyncExternalStore:(_subscribe:unknown,snapshot:()=>unknown)=>snapshot(),
}))
vi.mock('@/i18n',async original=>{const module=await original<typeof import('@/i18n')>();return {...module,useLocale:module.getLocale}})
vi.mock('@/lib/voiceRecorder',()=>({createVoiceRecorder:capture.create}))
vi.mock('@/lib/http',async original=>({...await original<typeof import('@/lib/http')>(),request:(...args:unknown[]) => (args[0]==='/api/v1/voice/preparations' ? capture.prepare : capture.request)(...args)}))
let action:(value:any)=>void
const bridge={ready:vi.fn(),phase:vi.fn(),complete:vi.fn(),cancel:vi.fn(),onAction:vi.fn()}
const render=()=>{hooks.cursor=0;return VoiceCapture()}
function text(node:unknown):string {
  if(typeof node==='string')return node
  if(Array.isArray(node))return node.map(text).join('')
  return node&&typeof node==='object'?text((node as ReactElement<{children?:unknown}>).props?.children):''
}
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return {promise,resolve}}
beforeEach(()=>{
  hooks.cursor=0;hooks.slots=[];hooks.cleanups=[]
  capture.create.mockReset();capture.request.mockReset();capture.prepare.mockReset().mockResolvedValue({ok:true,data:{ready:true}})
  bridge.ready.mockReset().mockResolvedValue({ok:true,data:{action:'start',id:'first'}})
  bridge.phase.mockReset().mockResolvedValue({ok:true,data:null})
  bridge.complete.mockReset().mockResolvedValue({ok:true,data:{result:'draft'}})
  bridge.cancel.mockReset().mockResolvedValue({ok:true,data:null})
  bridge.onAction.mockReset().mockImplementation(listener=>{action=listener;return vi.fn()})
  vi.stubGlobal('window',{harnessDesktop:{voice:bridge},addEventListener:vi.fn(),removeEventListener:vi.fn()})
  vi.stubGlobal('document',{documentElement:{}})
  capture.create.mockImplementation(()=>({ready:Promise.resolve({finish:async()=>new ArrayBuffer(48),dispose:vi.fn()}),dispose:vi.fn()}))
  capture.request.mockResolvedValue({ok:true,data:{transcript:'original speech'}})
})
afterEach(async()=>{hooks.cleanups.reverse().forEach(cleanup=>cleanup());vi.unstubAllGlobals();await setLocale('en')})

it('keeps browser-only voice feedback and lang synchronized with storage events and removes its listener',async()=>{
  const addEventListener=vi.fn(),removeEventListener=vi.fn()
  vi.stubGlobal('window',{addEventListener,removeEventListener})
  render()
  expect(text(render())).toContain(t('ui.voice.global.desktopRequired'))
  const listener=addEventListener.mock.calls.find(([name])=>name==='storage')![1]
  for(const language of ['en','zh-CN','en'] as const){
    listener({key:'harness.language.effective.v1',newValue:language})
    await vi.waitFor(()=>expect(document.documentElement.lang).toBe(language))
    expect(text(render())).toContain(t('ui.voice.global.desktopRequired'))
  }
  expect(capture.create).not.toHaveBeenCalled()
  expect(capture.prepare).not.toHaveBeenCalled()
  expect(capture.request).not.toHaveBeenCalled()
  expect(bridge.ready).not.toHaveBeenCalled()
  hooks.cleanups.splice(0).reverse().forEach(cleanup=>cleanup())
  expect(removeEventListener).toHaveBeenCalledExactlyOnceWith('storage',listener)
})

it('owns one recorder across ready and duplicate start, then submits and inserts its result only once',async()=>{
  render()
  await vi.waitFor(()=>expect(bridge.phase).toHaveBeenCalledWith('first','recording'))
  action({action:'start',id:'first'})
  expect(capture.create).toHaveBeenCalledOnce()
  action({action:'stop',id:'first'});action({action:'stop',id:'first'})
  await vi.waitFor(()=>expect(bridge.complete).toHaveBeenCalledExactlyOnceWith('first','original speech'))
  for(const language of ['en','zh-CN','en'] as const){await setLocale(language);expect(text(render())).toContain(t('ui.voice.global.draft'))}
  expect(capture.request).toHaveBeenCalledOnce()
  expect(capture.prepare).toHaveBeenCalledOnce()
})

it.each(['success','cancel','failure'])('shares preparation started during recording through %s without replaying audio',async kind=>{
  const preparation=deferred<any>()
  capture.prepare.mockReturnValue(preparation.promise)
  render();await vi.waitFor(()=>expect(bridge.phase).toHaveBeenCalledWith('first','recording'))
  expect(capture.prepare).toHaveBeenCalledOnce()
  action({action:'stop',id:'first'})
  await vi.waitFor(()=>expect(bridge.phase).toHaveBeenCalledWith('first','processing'))
  expect(capture.request).not.toHaveBeenCalled()
  if(kind==='cancel')action({action:'cancel'})
  preparation.resolve(kind==='failure'?{ok:false,status:503,code:'voice/unavailable',message:'failed',details:{copy:{key:'ui.voice.engineFailed'}}}:{ok:true,data:{ready:true}})
  if(kind==='success')await vi.waitFor(()=>expect(bridge.complete).toHaveBeenCalledOnce())
  else if(kind==='failure')await vi.waitFor(()=>expect(text(render())).toContain(t('ui.voice.engineFailed')))
  else {await preparation.promise;await Promise.resolve();await Promise.resolve()}
  expect(capture.request).toHaveBeenCalledTimes(kind==='success'?1:0)
  expect(capture.prepare).toHaveBeenCalledOnce()
})

it.each(['cancel','replace','unmount'] as const)('does not insert an accepted transcription after %s',async kind=>{
  const response=deferred<any>()
  capture.request.mockReturnValue(response.promise)
  render();await vi.waitFor(()=>expect(bridge.phase).toHaveBeenCalledWith('first','recording'))
  action({action:'stop',id:'first'})
  await vi.waitFor(()=>expect(capture.request).toHaveBeenCalledOnce())
  if(kind==='cancel')action({action:'cancel'})
  if(kind==='replace')action({action:'start',id:'second'})
  if(kind==='unmount')hooks.cleanups.splice(0).reverse().forEach(cleanup=>cleanup())
  response.resolve({ok:true,data:{transcript:'late speech'}})
  await response.promise
  await Promise.resolve();await Promise.resolve()
  expect(bridge.complete).not.toHaveBeenCalled()
  if(kind==='replace')await vi.waitFor(()=>expect(bridge.phase).toHaveBeenCalledWith('second','recording'))
})

it('disposes a late microphone grant after cancellation without recording or posting audio',async()=>{
  const permission=deferred<any>(),dispose=vi.fn()
  capture.create.mockReturnValue({ready:permission.promise,dispose})
  render();await vi.waitFor(()=>expect(capture.create).toHaveBeenCalledOnce())
  action({action:'cancel'})
  permission.resolve({finish:vi.fn(),dispose})
  await permission.promise;await Promise.resolve()
  expect(dispose).toHaveBeenCalled()
  expect(bridge.phase).not.toHaveBeenCalledWith('first','recording')
  expect(capture.request).not.toHaveBeenCalled()
})

it('preserves an unconfirmed insertion as a localized warning and never repeats it',async()=>{
  bridge.complete.mockResolvedValue({ok:false,copy:{key:'ui.voice.global.pasteUnconfirmed'},diagnostic:'original native error'})
  render();await vi.waitFor(()=>expect(bridge.phase).toHaveBeenCalledWith('first','recording'))
  action({action:'stop',id:'first'})
  await vi.waitFor(()=>expect(text(render())).toContain(t('ui.voice.global.pasteUnconfirmed')))
  for(const language of ['en','zh-CN','en'] as const){await setLocale(language);expect(text(render())).toContain(t('ui.voice.global.pasteUnconfirmed'))}
  action({action:'stop',id:'first'})
  expect(bridge.complete).toHaveBeenCalledOnce()
  expect(text(render())).toContain('original native error')
})

it('a failed microphone releases ownership and never submits an empty recording',async()=>{
  const dispose=vi.fn()
  capture.create.mockReturnValue({ready:Promise.reject(Error('device failed')),dispose})
  render()
  await vi.waitFor(()=>expect(text(render())).toContain(t('ui.voice.micFailed')))
  expect(dispose).toHaveBeenCalled()
  expect(bridge.phase).toHaveBeenCalledWith('first','done')
  expect(capture.request).not.toHaveBeenCalled()
})

it('keeps system authorization guidance bilingual without recording or transcribing',async()=>{
  capture.create.mockImplementation(()=>({ready:Promise.reject(new ResourceError({key:'ui.systemPermission.microphone.settings'})),dispose:vi.fn()}))
  render()
  await vi.waitFor(()=>expect(bridge.phase).toHaveBeenCalledWith('first','done'))
  for(const locale of ['en','zh-CN','en'] as const){await setLocale(locale);expect(text(render())).toContain(t('ui.systemPermission.microphone.settings'))}
  expect(capture.create).toHaveBeenCalledOnce()
  expect(capture.request).not.toHaveBeenCalled()
  action({action:'start',id:'second'})
  await vi.waitFor(()=>expect(bridge.phase).toHaveBeenCalledWith('second','done'))
  expect(capture.create).toHaveBeenCalledTimes(2)
})

it('shows resource-only accessibility guidance without redundant technical details or replaying permission requests',async()=>{
  bridge.ready.mockResolvedValue({ok:true,data:{action:'error',copy:{key:'ui.systemPermission.accessibility.settings'}}})
  render()
  await vi.waitFor(()=>expect(text(render())).toContain(t('ui.systemPermission.accessibility.settings')))
  for(const locale of ['en','zh-CN','en'] as const){
    await setLocale(locale)
    const output=text(render())
    expect(output).toContain(t('ui.systemPermission.accessibility.settings'))
    expect(output).not.toContain(t('ui.voice.diagnostic'))
    expect(output).not.toContain('System input permission required')
  }
  expect(bridge.ready).toHaveBeenCalledOnce()
  expect(capture.create).not.toHaveBeenCalled()
  expect(capture.request).not.toHaveBeenCalled()
})
