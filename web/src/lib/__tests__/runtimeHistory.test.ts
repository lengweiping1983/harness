import {expect,it} from 'vitest'
import {validateRuntimeHistory} from '../runtimeHistory'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {setLocale,t} from '@/i18n'

it.each([
 [[],true,'next'],[[{id:'one'}],true,'before'],[[{id:'one'}],false,'next'],
 [[{id:'same'},{id:'same'}],false,null],[[{id:''}],false,null],[[{id:'one'}],true,null],
] as const)('rejects incomplete or stalled history pages', (rows,more,next)=>{
 let error:unknown
 try{validateRuntimeHistory([...rows],more,next,'before',row=>row.id)}catch(value){error=value}
 expect(failureText(error)).toEqual({key:'ui.http.invalidResponse'})
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(failureText(error))).toBe(t('ui.http.invalidResponse'))}
 setLocale('zh-CN')
})
it('accepts advancing and final pages without rewriting original rows',()=>{
 const rows=[{id:'one',label:'Original 原文'}]
 validateRuntimeHistory(rows,true,'next','before',row=>row.id)
 validateRuntimeHistory(rows,false,null,'before',row=>row.id)
 validateRuntimeHistory<{id:string}>([],false,null,'before',row=>row.id)
 expect(rows).toEqual([{id:'one',label:'Original 原文'}])
})
