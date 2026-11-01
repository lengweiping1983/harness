import {expect,it} from 'vitest'
import type {StreamEvent} from '@/types'
import {emptySession,reduceEvent} from '../chat'

it.each([true,false,-1,1.5,NaN,Infinity,'12',null,[],{}])('invalid usage counter %s preserves prior totals and original response',value=>{
 const state={...emptySession(),usage:{input_tokens:10,output_tokens:2,total_tokens:12,requests:1}}
 const patch=reduceEvent(state,{type:'assistant/message',text:'Original 原文',usage:{input_tokens:value}} as unknown as StreamEvent)
 expect(patch.usage).toBeUndefined()
 expect(patch.error).toEqual({key:'ui.http.invalidResponse'})
 expect(patch.blocks?.some(block=>block.kind==='assistant'&&block.text==='Original 原文')).toBe(true)
 expect(state.usage.total_tokens).toBe(12)
})
it('safe counters accumulate once and overflow cannot corrupt confirmed totals',()=>{
 const state={...emptySession(),usage:{input_tokens:10,output_tokens:2,requests:1}}
 const patch=reduceEvent(state,{type:'assistant/message',text:'Original',usage:{input_tokens:3,output_tokens:1}} as StreamEvent)
 expect(patch.usage).toMatchObject({input_tokens:13,output_tokens:3,total_tokens:16,requests:2})
 const overflow=reduceEvent(state,{type:'assistant/message',usage:{input_tokens:Number.MAX_SAFE_INTEGER}} as StreamEvent)
 expect(overflow.usage).toBeUndefined();expect(overflow.error).toBeTruthy()
})
it('ready without a draft clears stale connection text while explicit draft preserves original',()=>{
 const state={...emptySession(),draft:{text:'Obsolete',thinking:'Obsolete',turn:1},turn:1}
 const empty=reduceEvent(state,{type:'ready',last_seq:1,running:false} as unknown as StreamEvent)
 expect(empty.draft).toEqual({text:'',thinking:'',turn:1})
 const next=reduceEvent(state,{type:'ready',last_seq:1,running:true,draft:{text:'Original 原文',thinking:'Thought',turn:2}} as unknown as StreamEvent)
 expect(next.draft).toMatchObject({text:'Original 原文',thinking:'Thought',turn:2})
})
