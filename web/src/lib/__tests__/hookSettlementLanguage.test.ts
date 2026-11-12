import {afterEach,expect,it} from 'vitest'
import {setLocale,t} from '@/i18n'
import type {HookActivity} from '@/types'
import {settleHookActivity} from '../hookActivity'
import {noticeText} from '../noticeText'
afterEach(()=>setLocale('zh-CN'))
it('settled local reasons retain resources while external terminal reasons stay unchanged',()=>{
 const running={id:'running',status:'running',reason:'Original progress 原文'} as HookActivity
 const finished={id:'done',status:'failed',reason:'Original failure 原文'} as HookActivity
 setLocale('en')
 const rows=settleHookActivity([running,finished])!
 expect(running.status).toBe('running');expect(rows[1]).toBe(finished)
 expect(rows[0].status).toBe('interrupted')
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(rows[0].reasonCopy!)).toBe(t('ui.lib.hookActivity.c28f6401fb6d'));expect(rows[1].reason).toBe('Original failure 原文')}
 expect(settleHookActivity(rows)![0]).toBe(rows[0])
})
