import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import type {ReactElement} from 'react'
import {VoiceCapture} from '../VoiceCapture'
import {setLocale,t} from '@/i18n'

const hooks=vi.hoisted(()=>({cursor:0,slots:[] as unknown[],cleanups:[] as (()=>void)[]}))
const capture=vi.hoisted(()=>({create:vi.fn(),request:vi.fn()}))
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
vi.mock('@/lib/http',async original=>({...await original<typeof import('@/lib/http')>(),request:capture.request}))
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
  capture.create.mockReset();capture.request.mockReset()
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

it('owns one recorder across ready and duplicate start, then submits and inserts its result only once',async()=>{
  render()
  await vi.waitFor(()=>expect(bridge.phase).toHaveBeenCalledWith('first','recording'))
  action({action:'start',id:'first'})
  expect(capture.create).toHaveBeenCalledOnce()
  action({action:'stop',id:'first'});action({action:'stop',id:'first'})
  await vi.waitFor(()=>expect(bridge.complete).toHaveBeenCalledExactlyOnceWith('first','original speech'))
  for(const language of ['en','zh-CN','en'] as const){await setLocale(language);expect(text(render())).toContain(t('ui.voice.global.draft'))}
  expect(capture.request).toHaveBeenCalledOnce()
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
