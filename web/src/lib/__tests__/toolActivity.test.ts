import {afterEach, expect, it} from 'vitest'
import {setLocale} from '@/i18n'
import type {ToolBlockState} from '@/types'
import {toolActivity, toolActivityKind} from '../toolActivity'
import {groupToolRuns} from '../toolRuns'

const block=(name:string,status:ToolBlockState['status']='ok'):ToolBlockState=>({kind:'tool',id:name,callId:name,turn:1,at:1,name,status,input:{description:'Original-{name} 原文',target:'Original-target'},output:'Original result 原文'})
afterEach(()=>setLocale('zh-CN'))
it('classifies current Agent controls and groups spawn/followup as Agent collaboration',()=>{
 for(const name of ['spawn_agent','followup_task','list_agents','wait_agent','wait_agents','send_message','interrupt_agent'])expect(toolActivityKind(block(name))).toBe('agent')
 expect(toolActivityKind(block('subagent'))).toBe('tool')
 const rows=[block('spawn_agent'),block('followup_task')].map(block=>({kind:'block' as const,id:block.id,block}))
 for(const locale of ['en','zh-CN','en'] as const){
  setLocale(locale)
  expect(groupToolRuns(rows,{})[0]).toMatchObject({kind:'tool-run',activityKind:'agent',summary:locale==='en'?'Agent collaboration 2 times':'智能体协作 2 次'})
 }
})
it('renders current spawn/followup states with complete language templates and unchanged details',()=>{
 for(const locale of ['en','zh-CN','en'] as const){
  setLocale(locale)
  for(const name of ['spawn_agent','followup_task'])for(const status of ['running','error','ok'] as const){
   const source=block(name,status),original=JSON.stringify(source)
   const action=locale==='en'?(name==='spawn_agent'?'Start a subagent':'Continue the Agent task'):(name==='spawn_agent'?'启动子智能体':'继续智能体任务')
   const label=locale==='en'?(status==='running'?'In progress: ':status==='error'?'Failed: ':'Completed: ')+action:status==='running'?'正在'+action:status==='error'?action+'失败':'已'+action
   expect(toolActivity(source)).toMatchObject({kind:'agent',label,detail:'Original-{name} 原文'})
   expect(JSON.stringify(source)).toBe(original)
  }
 }
})
it('preserves unknown external tool names and explicit nonexecution evidence',()=>{
 for(const locale of ['en','zh-CN'] as const){
  setLocale(locale)
  const external=toolActivity(block('ThirdParty-{name} 原文','error'))
  expect(external.kind).toBe('tool');expect(external.label).toContain('ThirdParty-{name} 原文')
  const skipped=toolActivity({...block('spawn_agent','error'),display:{not_executed:true}})
  expect(skipped.label).toBe(locale==='en'?'Skipped; tool not executed':'已跳过，工具未执行')
 }
})
