import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {useSessionPanelOpener} from '../useSessionPanelOpener'
import {CollaborationButton} from '@/components/shared/CollaborationButton'
import {useAppStore} from '@/store/app'
import {setLocale, t} from '@/i18n'

const hooks=vi.hoisted(() => ({cursor:0,slots:[] as any[],cleanups:new Map<number,()=>void>()}))
vi.mock('react',async original => ({...await original<typeof import('react')>(),
  useRef:(value:unknown) => {const i=hooks.cursor++; return hooks.slots[i] ??= {current:value}},
  useState:(value:unknown) => {const i=hooks.cursor++;if(!(i in hooks.slots))hooks.slots[i]=value;return [hooks.slots[i],(next:any) => {hooks.slots[i]=typeof next==='function'?next(hooks.slots[i]):next}]},
  useLayoutEffect:(effect:()=>void|(()=>void),deps:unknown[]) => {
    const i=hooks.cursor++,old=hooks.slots[i]
    if(!old||deps.some((value,index)=>value!==old[index])) {hooks.cleanups.get(i)?.();hooks.slots[i]=deps;const cleanup=effect();if(cleanup)hooks.cleanups.set(i,cleanup)}
  },
}))
vi.mock('@/i18n',async original => {const module=await original<typeof import('@/i18n')>();return {...module,useLocale:module.getLocale}})
const initial=useAppStore.getState()
const create=vi.fn()
const render=() => {hooks.cursor=0;return useSessionPanelOpener(useAppStore.getState().activeSessionId)}
const unmount=() => {for(const cleanup of hooks.cleanups.values())cleanup();hooks.cleanups.clear()}
beforeEach(() => {
  hooks.cursor=0;hooks.slots=[];hooks.cleanups.clear();create.mockReset()
  useAppStore.setState({activeSessionId:null,newSessionRevision:1,materializeSession:create})
})
afterEach(async () => {unmount();useAppStore.setState(initial);await setLocale('en')})

it('keeps the actual invitation control visible before and after a conversation is created without creating on render',async () => {
  for(const locale of ['en','zh-CN','en'] as const) {
    await setLocale(locale)
    for(const sessionId of [null,'existing']) {
      const html=renderToStaticMarkup(<CollaborationButton sessionId={sessionId} open={false} onOpen={()=>{}} onClose={()=>{}} />)
      expect(html).toContain(t('ui.collaboration.invite'))
      expect(html).not.toMatch(/\sdisabled(?:=|\s|>)/)
    }
  }
  expect(create).not.toHaveBeenCalled()
})

it('only an explicit open creates a conversation and opens the panel for its confirmed identity',async () => {
  create.mockImplementation(async () => {useAppStore.setState({activeSessionId:'created'});return true})
  const opener=render()
  expect(create).not.toHaveBeenCalled()
  await opener.open()
  expect(create).toHaveBeenCalledOnce()
  expect(render().request).toEqual({sessionId:'created',serial:1})
  render().close()
  expect(render().request).toBeNull()
  await render().open()
  expect(create).toHaveBeenCalledOnce()
  expect(render().request?.sessionId).toBe('created')
})

it.each(['unmount','replace','rejected'])('does not open a panel after creation is %s',async kind => {
  let finish!:(value:boolean)=>void
  create.mockReturnValue(new Promise(resolve => {finish=resolve}))
  const opening=render().open()
  if(kind==='unmount')unmount()
  useAppStore.setState({activeSessionId:kind==='replace'?'another':'created',newSessionRevision:kind==='replace'?2:1})
  finish(kind!=='rejected');await opening
  expect(hooks.slots[1]).toBeNull()
  expect(create).toHaveBeenCalledOnce()
})
