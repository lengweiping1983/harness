import {afterEach,expect,it} from 'vitest'
import {setLocale,t} from '@/i18n'
import type {NoticeText,ResourceTemplate} from '@/types'
import {noticeText} from '../noticeText'
import {permissionDecision} from '../permissions'
import {ResourceError,failureText} from '../resourceError'

afterEach(()=>setLocale('zh-CN'))
it('resolves saved nested resource parameters while preserving raw text and numeric values',()=>{
 const raw='Original 原文 {status} <script>value</script>'
 const text:ResourceTemplate={template:{key:'ui.permissions.with_reason',params:{decision:{key:'ui.environment.result',params:{count:2,skipped:3}},reason:{template:{key:'ui.permissions.with_reason',params:{decision:raw,reason:{key:'ui.http.failure',params:{status:503}}}}}}}}
 const original=JSON.stringify(text)
 setLocale('en');const failure=new ResourceError(text)
 for(const locale of ['en','zh-CN','en'] as const){
  setLocale(locale)
  const expected=t('ui.permissions.with_reason',{decision:t('ui.environment.result',{count:2,skipped:3}),reason:t('ui.permissions.with_reason',{decision:raw,reason:t('ui.http.failure',{status:503})})})
  expect(noticeText(text)).toBe(expected)
  expect(noticeText(failureText(failure))).toBe(expected)
  expect(noticeText(raw)).toBe(raw)
  expect(noticeText({template:{key:'ui.environment.result',params:{count:0,skipped:2}}})).toBe(t('ui.environment.result',{count:0,skipped:2}))
  expect(JSON.stringify(text)).toBe(original)
 }
})
it('keeps approval tool and reviewer reason raw while translating the current decision',()=>{
 const approval:NoticeText={approval:{decisions:['allow','deny','ask','constructor'],reviewer:'auto_review',reason:'Original reason 原文',tool:'MCP.fixture_tool'}}
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(approval)).toBe(t('ui.store.chat.c36db9f5daed',{p0:permissionDecision(approval.approval.decisions,'auto_review','Original reason 原文'),p1:'MCP.fixture_tool'}))}
})
