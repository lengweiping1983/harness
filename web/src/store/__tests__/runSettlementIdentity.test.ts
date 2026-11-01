import {expect,it} from 'vitest'
import type {StreamEvent} from '@/types'
import {emptySession,reduceEvent} from '../chat'
import {mergeTurnActivity} from '@/lib/turnActivity'

it.each(['completed','failed','interrupted'])('late %s receipt and its done frame cannot settle a newer run',status=>{
 const initial=emptySession()
 const started={...initial,...reduceEvent(initial,{type:'turn/start',turn:2,run_id:'current',seq:4} as unknown as StreamEvent)}
 const state={...started,draft:{text:'Current original 原文',thinking:'',turn:2}}
 for(const event of [{type:'run/state',id:'previous',status,started_at:1,seq:5},{type:'done',run_id:'previous',seq:6}]){
  const patch=reduceEvent(state,event as StreamEvent)
  expect(patch.running).toBeUndefined();expect(patch.draft).toBeUndefined();expect(patch.blocks).toBeUndefined()
 }
 const settled=reduceEvent(state,{type:'run/state',id:'current',status,started_at:1,seq:7} as StreamEvent)
 expect(settled.running).toBe(false)
 expect(settled.blocks?.some(block=>block.kind==='assistant'&&block.text==='Current original 原文')).toBe(true)
})
it('restored turn activity preserves the persisted run identity',()=>{
 const turns=mergeTurnActivity({},[{type:'turn/start',seq:1,time:1,data:{turn:1,run_id:'original'}},
  {type:'turn/end',seq:2,time:2,data:{turn:1,reason:'completed'}}])
 expect(turns[1].runId).toBe('original')
})
