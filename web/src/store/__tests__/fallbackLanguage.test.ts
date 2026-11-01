import { afterEach, expect, it, vi } from 'vitest'
import { setLocale, t } from '@/i18n'
import { noticeText } from '@/lib/noticeText'
import { ApiError } from '@/lib/http'
import * as http from '@/lib/http'
import { blocksFromDetail, emptySession, reduceEvent, useChatStore } from '../chat'
import type { ErrorBlock, SessionDetail, StreamEvent, ToolBlockState } from '@/types'

const detail = (): SessionDetail => ({ header: { id: 'fixture' }, events: [], messages: [], usage: {}, stats: {}, todos: [], timeline: [],
  events_view: 'conversation-window', history: { through: 20, start: 10, end: 20, total: 20, has_more: true, next_cursor: 'cursor', user_messages: [] } })
afterEach(() => { vi.restoreAllMocks(); setLocale('zh-CN'); useChatStore.setState({ sessions: {} }) })

it('interrupted tool fallbacks retain keys and a real result replaces them', () => {
  setLocale('en')
  const snapshot = detail()
  snapshot.interrupted = true
  snapshot.events = [{ type: 'tool/call', seq: 1, data: { call_id: 'call', name: 'fixture', turn: 1, status: 'running' } }]
  const restored = blocksFromDetail(snapshot).find(block => block.kind === 'tool') as ToolBlockState
  expect(restored.output).toBeUndefined()
  expect(restored.outputKey).toBe('ui.store.chat.eda14e9f81aa')
  const state = { ...emptySession(), turn: 1, blocks: [{ id: 'tool', kind: 'tool', at: 0, turn: 1, callId: 'call', name: 'fixture', status: 'running' } as ToolBlockState] }
  const settled = { ...state, ...reduceEvent(state, { type: 'turn/end', turn: 1, reason: 'interrupted' } as StreamEvent) }
  const tool = settled.blocks[0] as ToolBlockState
  expect(tool.outputKey).toBe('ui.store.chat.a57ff6bf2376')
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    expect(noticeText({ key: tool.outputKey! })).toBe(t('ui.store.chat.a57ff6bf2376'))
    expect(noticeText({ key: restored.outputKey! })).toBe(t('ui.store.chat.eda14e9f81aa'))
  }
  const finished = reduceEvent(settled, { type: 'tool/end', call_id: 'call', turn: 1, content: 'Original output 原文', is_error: false } as StreamEvent)
  expect((finished.blocks![0] as ToolBlockState).outputKey).toBeUndefined()
  expect((finished.blocks![0] as ToolBlockState).output).toBe('Original output 原文')
})

it('unknown runtime and terminal failures retain resource keys, supplied errors stay original', () => {
  setLocale('en')
  const runtime = reduceEvent(emptySession(), { type: 'error' } as StreamEvent)
  const terminal = reduceEvent(emptySession(), { type: 'turn/end', reason: { kind: 'error' } } as StreamEvent)
  expect((runtime.blocks![0] as ErrorBlock).messageKey).toBe('ui.store.chat.13a46616e16f')
  expect((terminal.blocks![0] as ErrorBlock).messageKey).toBe('ui.lib.turnOutcome.ac8aaa680b6c')
  const raw = reduceEvent(emptySession(), { type: 'error', message: 'Original failure 原文' } as StreamEvent)
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    expect(noticeText(runtime.error!)).toBe(t('ui.store.chat.13a46616e16f'))
    expect(noticeText(terminal.error!)).toBe(t('ui.lib.turnOutcome.ac8aaa680b6c'))
    expect(raw.error).toBe('Original failure 原文')
  }
})

it.each(['mismatch', 'identity', 'unknown', 'transport', 'original'] as const)('history %s preserves identity across language switches', async mode => {
  setLocale('en')
  useChatStore.getState().hydrate('fixture', detail())
  const send = vi.spyOn(http, 'runtimeRequest')
  if (mode === 'mismatch') send.mockResolvedValue({ ...detail(), history: { ...detail().history!, through: 21 } })
  else if (mode === 'identity') send.mockResolvedValue({ ...detail(), header: { id: 'other-conversation' }, history: { ...detail().history!, start: 0, end: 10 }, messages: [{ kind: 'UserMessage', role: 'user', source_seq: 1, text: 'Other conversation private text' }] })
  else if (mode === 'unknown') send.mockRejectedValue('unknown')
  else if (mode === 'transport') send.mockRejectedValue(new ApiError({ ok: false, status: 0, code: 'timeout', message: 'old translation', copy: { key: 'ui.lib.api.8b7f52f0dbb6' } }))
  else send.mockRejectedValue(new Error('Original reason 原文'))
  await useChatStore.getState().loadOlder('fixture')
  const history = useChatStore.getState().get('fixture').history!
  expect(useChatStore.getState().get('fixture').blocks.some(block => block.kind === 'user' && block.text === 'Other conversation private text')).toBe(false)
  if (mode === 'identity') expect(history.resetRequired).toBe(true)
  const key = mode === 'mismatch' || mode === 'identity' ? 'ui.store.chat.d15acd329968' : mode === 'unknown' ? 'ui.store.chat.d368dae767d0' : 'ui.lib.api.8b7f52f0dbb6'
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    expect(noticeText(history.error!)).toBe(mode === 'original' ? 'Original reason 原文' : t(key))
  }
  expect(send).toHaveBeenCalledTimes(1)
})

it('retains run failure resources from live events and restored terminal evidence',()=>{
 const copy={key:'ui.run.incomplete',params:{reason:'Original reason 原文'}}
 for(const error of [{code:'run/incomplete',message:'Original diagnostic 原文',copy},{code:'run/incomplete',message:'Original diagnostic 原文',details:{copy}}]){
  const live=reduceEvent(emptySession(),{type:'error',...error} as StreamEvent)
  const terminal=reduceEvent(emptySession(),{type:'turn/end',turn:1,reason:{kind:'error',error}} as StreamEvent)
  const snapshot=detail();snapshot.events=[{type:'turn/end',seq:12,data:{turn:1,reason:{kind:'error',error}}}]
  const historical=blocksFromDetail(snapshot).find(block=>block.kind==='error') as ErrorBlock
  for(const block of [live.blocks![0],terminal.blocks![0],historical] as ErrorBlock[]){
   expect(block.messageCopy).toEqual(copy);expect(block.message).toBe('Original diagnostic 原文')
   for(const language of ['en','zh-CN','en'] as const){setLocale(language);expect(noticeText(block.messageCopy!)).toBe(t(copy.key,copy.params))}
  }
  expect(live.error).toEqual(copy);expect(terminal.error).toEqual(copy)
 }
})

it('keeps explicit resource identity when duplicate live and terminal errors converge',()=>{
 const copy={key:'ui.run.incomplete',params:{reason:'Original reason'}}
 const error={code:'run/incomplete',message:'Original diagnostic',copy}
 const initial={...emptySession(),turn:1}
 const first={...initial,...reduceEvent(initial,{type:'error',...error} as StreamEvent)}
 const duplicate=reduceEvent(first,{type:'error',...error} as StreamEvent)
 expect(duplicate.blocks).toHaveLength(1)
 expect((duplicate.blocks![0] as ErrorBlock).messageCopy).toEqual(copy)
 const terminal=reduceEvent(first,{type:'turn/end',turn:1,reason:{kind:'error',error}} as StreamEvent)
 expect(terminal.blocks).toHaveLength(1)
 expect((terminal.blocks![0] as ErrorBlock).messageCopy).toEqual(copy)
 expect(terminal.error).toEqual(copy)
})


it.each(['turn','code'] as const)('does not merge matching diagnostics from another %s',field=>{
 const previous={id:'prior',kind:'error' as const,at:0,turn:1,code:'original/code',message:'Same diagnostic'}
 const state={...emptySession(),turn:2,blocks:[previous]}
 const outcome=field==='turn'?{turn:2,code:'original/code'}:{turn:1,code:'another/code'}
 const patch=reduceEvent({...state,turn:outcome.turn},{type:'turn/end',turn:outcome.turn,
  reason:{kind:'error',error:{code:outcome.code,message:'Same diagnostic',copy:{key:'ui.run.executionFailed'}}}} as StreamEvent)
 expect(patch.blocks).toHaveLength(2)
 expect(patch.blocks![0]).toBe(previous)
 expect((patch.blocks![0] as ErrorBlock).messageCopy).toBeUndefined()
 expect((patch.blocks![1] as ErrorBlock).messageCopy).toEqual({key:'ui.run.executionFailed'})
})


it.each(['different-code','prefix-only'] as const)('preserves distinct live errors with %s diagnostics',mode=>{
 const previous={id:'prior',kind:'error' as const,at:0,turn:1,code:'original/code',
  message:mode==='prefix-only'?'[original/code] Same diagnostic with extra evidence':'Same diagnostic'}
 const state={...emptySession(),turn:1,blocks:[previous]}
 const patch=reduceEvent(state,{type:'error',code:mode==='different-code'?'another/code':'original/code',
  message:'Same diagnostic',copy:{key:'ui.run.executionFailed'}} as StreamEvent)
 expect(patch.blocks).toHaveLength(2)
 expect(patch.blocks![0]).toBe(previous)
 expect((patch.blocks![0] as ErrorBlock).messageCopy).toBeUndefined()
 expect((patch.blocks![1] as ErrorBlock).message).toBe('Same diagnostic')
})

it('merges the exact code-prefixed terminal diagnostic with its matching live event',()=>{
 const previous={id:'prior',kind:'error' as const,at:0,turn:1,code:'original/code',message:'[original/code] Same diagnostic'}
 const patch=reduceEvent({...emptySession(),turn:1,blocks:[previous]},
  {type:'error',code:'original/code',message:'Same diagnostic',copy:{key:'ui.run.executionFailed'}} as StreamEvent)
 expect(patch.blocks).toHaveLength(1)
 expect((patch.blocks![0] as ErrorBlock).messageCopy).toEqual({key:'ui.run.executionFailed'})
})
