import { describe, it, expect } from 'vitest'
import { setLocale } from '@/i18n'
import { groupToolRuns } from '../toolRuns'
import type { Block } from '@/types'
import type { ConversationRow } from '../turnActivity'
const thought = (id: string, turn = 1): Block => ({kind:'thinking', id, turn, at:1, text:'\u539f\u6587\u{1f600}'})
const tool = (id: string, status: 'ok' | 'running' | 'error' = 'ok'): Block => ({kind:'tool', id, turn:1, at:1, callId:id, name:'acceptance_evidence', status})
const rows = (blocks: Block[]): ConversationRow[] => blocks.map(block => ({kind:'block',id:block.id,block}))
describe('process activity grouping', () => {
  it('collapses five thinking segments and four tools; expands original rows once in order', () => {
    const input = rows([thought('a'),tool('b'),thought('c'),tool('d'),thought('e'),tool('f'),thought('g'),tool('h'),thought('i')])
    const identities = new Map<string,string>()
    for (const locale of ['en','zh-CN','en'] as const) {
      setLocale(locale)
      const collapsed = groupToolRuns(input, {}, identities)
      expect(collapsed).toHaveLength(1)
      expect(collapsed[0]).toMatchObject({kind:'tool-run', count:9, summary:locale === 'en' ? 'Thinking segments: 5 · Tool calls: 4' : '5 段思考 · 4 次工具调用'})
      const expanded = groupToolRuns(input, {[collapsed[0]!.id]:true}, identities)
      expect(expanded.slice(1).map(row => row.id)).toEqual(input.map(row => row.id))
      expect(expanded.slice(1)).toEqual(input)
    }
  })
  it('preserves narrative, turn and identity boundaries and exposes running/error tools', () => {
    const input = rows([thought('a'),tool('b','error'),tool('c','running'),thought('d'),{kind:'assistant',id:'reply',at:1,turn:1,text:'Answer'},thought('e',2),thought('f',2)])
    const output = groupToolRuns(input,{})
    expect(output.map(row => row.id)).toEqual(['tool-run:1:a','b','c','reply','tool-run:2:e'])
    expect(output[0]).toMatchObject({failed:1,running:1})
    const unknown = rows([{...thought('x'),turn:undefined},{...thought('y'),turn:undefined}])
    expect(groupToolRuns(unknown,{})).toEqual(unknown)
  })
})

it('keeps commentary and user messages between independently foldable activity segments as streaming appends',()=>{
 const first=rows([thought('first-thought'),tool('first-tool')])
 const note:Block={kind:'assistant',id:'note',turn:1,at:2,text:'Progress original'}
 const user:Block={kind:'user',id:'steering',turn:1,at:3,text:'Steering original'}
 const input=[...first,...rows([note,thought('second-thought'),tool('second-tool'),user,thought('third-thought'),tool('third-tool','running')])]
 const aliases=new Map<string,string>()
 const original=groupToolRuns(first,{},aliases)[0]!.id
 const grouped=groupToolRuns(input,{[original]:true},aliases)
 expect(grouped.filter(row=>row.kind==='tool-run').map(row=>[row.id,row.open])).toEqual([
  [original,true],['tool-run:1:second-thought',false],['tool-run:1:third-thought',false],
 ])
 expect(grouped.map(row=>row.id)).toEqual([original,'first-thought','first-tool','note','tool-run:1:second-thought','steering','tool-run:1:third-thought','third-tool'])
 const prepended=groupToolRuns([...rows([thought('older')]),...input],{[original]:true},aliases)
 expect(prepended[0]!.id).toBe(original)
 expect(prepended.find(row=>row.id===original)).toMatchObject({open:true})
})

it.each([[1,1,1],[2,0,0],[2,2,2]])('process summaries omit character counts for %s thoughts and %s tools',(thoughts,characters,tools)=>{
 const blocks:Block[]=[...Array.from({length:thoughts},(_,index)=>({...thought('thought-'+index),text:index===0?'x'.repeat(characters):''})),...Array.from({length:tools},(_,index)=>tool('tool-'+index))]
 for(const locale of ['en','zh-CN','en'] as const){
  setLocale(locale)
  const result=groupToolRuns(rows(blocks),{})[0]
  expect(result).toMatchObject({kind:'tool-run',summary:locale==='en'
   ? `Thinking segments: ${thoughts}${tools ? ` · Tool calls: ${tools}` : ''}`
   : `${thoughts} 段思考${tools ? ` · ${tools} 次工具调用` : ''}`})
 }
})
it('authored thinking and replies preserve identity and separate adjacent process groups',()=>{
 const author={team:'team-original',member:'member-original',display_name:'Original 作者 {name}',role:'member' as const,stage:'reply' as const}
 const speaker={agent:'agent-original',display_name:'Original Agent {name}'}
 for(const identity of [{author},{speaker},{author,speaker}])for(const kind of ['thinking','assistant'] as const){
  const authored:Block={kind,id:'authored',at:1,turn:1,text:'Original 原文 {name}',...identity}
  const input=rows([tool('before-a'),tool('before-b'),authored,tool('after-a'),tool('after-b')])
  const output=groupToolRuns(input,{})
  expect(output.map(row=>row.id)).toEqual(['tool-run:1:before-a','authored','tool-run:1:after-a'])
  expect(output[1]).toBe(input[2])
  expect(authored).toMatchObject({text:'Original 原文 {name}'})
 }
})

it('keeps visualization calls outside collapsed activity groups through loading, success and failure', () => {
  for (const status of ['running', 'ok', 'error'] as const) {
    const widget: Block = {kind: 'tool', id: 'widget', callId: 'widget-call', turn: 1, at: 2, name: 'show_widget', status,
      display: status === 'ok' ? {widget: {title: 'Original', kind: 'html', code: '<button>Go</button>'}} : undefined}
    const input = rows([thought('before'), tool('read'), widget, thought('after'), tool('check')])
    const output = groupToolRuns(input, {})
    expect(output.map(row => row.id)).toEqual(['tool-run:1:before', 'widget', 'tool-run:1:after'])
    expect(output[1]).toBe(input[2])
  }
})
