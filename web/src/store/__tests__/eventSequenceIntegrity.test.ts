import {expect,it} from 'vitest'
import type {StreamEvent} from '@/types'
import {emptySession,reduceEvent} from '../chat'

it.each([true,false,'3',null,-1,1.5,NaN,Infinity,Number.MAX_SAFE_INTEGER+1])('rejects invalid event sequence %s without changing message or replay cursor',seq=>{
 const state={...emptySession(),lastSeq:2}
 const event={type:'assistant/delta',seq,delta:'Original 原文'} as unknown as StreamEvent
 expect(reduceEvent(state,event)).toEqual({})
 expect(state.lastSeq).toBe(2);expect(state.draft.text).toBe('')
})
it('keeps current integer, duplicate filtering and unsequenced control events',()=>{
 const state={...emptySession(),lastSeq:2}
 expect(reduceEvent(state,{type:'assistant/delta',seq:2,delta:'Duplicate'} as StreamEvent)).toEqual({})
 expect(reduceEvent(state,{type:'assistant/delta',seq:3,delta:'Original 原文'} as StreamEvent)).toMatchObject({lastSeq:3,draft:{text:'Original 原文'}})
 expect(reduceEvent(state,{type:'assistant/delta',delta:'Local 原文'} as StreamEvent)).toMatchObject({draft:{text:'Local 原文'}})
})
