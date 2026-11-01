import {noticeText} from '../noticeText'
import {afterEach,expect,it} from 'vitest'
import {setLocale,t} from '@/i18n'
import {browserActionLabel} from '../browser'
import {hookStatuses} from '../projectHooks'
import {CONTEXT_ROLE} from '../contextAudit'
import {requestDifferenceCopy,REQUEST_ROLE,REQUEST_STATUS,REQUEST_TERMINAL_REASON} from '../requestTraces'
afterEach(()=>setLocale('zh-CN'))
it.each([REQUEST_ROLE,REQUEST_STATUS,REQUEST_TERMINAL_REASON,CONTEXT_ROLE,browserActionLabel,hookStatuses])('lookup excludes inherited keys while preserving unknown originals',table=>{
 for(const key of ['constructor','toString','__proto__']){expect(table[key]).toBeUndefined();expect(table[key]??key).toBe(key)}
})
it('registered getters resolve the current language on every read',()=>{
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(browserActionLabel.navigate).toBe(t('ui.lib.browser.82d9ce3a5b37'));expect(hookStatuses.failed).toBe(t('ui.member.status.failed'));expect(CONTEXT_ROLE.UserMessage).toBe(t('ui.lib.contextAudit.e6be19b38c9e'));expect(REQUEST_ROLE.user).toBe(t('ui.lib.requestTraces.a7dfeaf0c12c'));expect(REQUEST_STATUS.started).toBe(t('ui.lib.requestTraces.cc1f354b0df6'));expect(REQUEST_TERMINAL_REASON.done).toBe(t('ui.lib.requestTraces.7809a5cd638a'))}
})

it.each([[false,false],[true,false],[false,true],[true,true]])('difference copy has a complete sentence, offset=%s order=%s',(offset,order)=>{
 const path='/Original 原文/{name}'
 const copy=requestDifferenceCopy({path,kind:order?'key_order':'value',...(offset?{text_offset:0}:{})})
 for(const locale of ['en','zh-CN','en'] as const){
  setLocale(locale)
  const expected=(locale==='en'?'Change location: ':'变化位置：')+path
   +(offset?(locale==='en'?' · Text offset 0':' · 文本偏移 0'):'')
   +(order?(locale==='en'?' · Field order changed':' · 字段顺序变化'):'')
  expect(noticeText(copy)).toBe(expected)
 }
})
