import {secondsLabel} from '../generation'
import {runDuration} from '../runHistory'
import { describe, expect, it, vi, afterEach } from 'vitest'
import {setLocale} from '@/i18n'
import { formatRelativeTime, formatDuration, formatElapsedDuration, toDate } from '../utils'

describe('time formatting boundaries', () => {
  it('carries rounded seconds into minutes and hours', () => {
    expect(formatDuration(119499)).toBe('1m 59s')
    expect(formatDuration(119500)).toBe('2m 0s')
    expect(formatDuration(3599500)).toBe('1h 0m')
  })
  it('rejects timestamps outside the Date range', () => {
    expect(toDate(1e20)).toBeNull()
    expect(toDate('100000000000000000000')).toBeNull()
    expect(toDate(1700000000)?.getTime()).toBe(1700000000000)
    expect(toDate(1700000000000)?.getTime()).toBe(1700000000000)
  })
})

it('handles just-now and singular/plural relative times across repeated language changes',()=>{
 const now=new Date('2026-10-27T12:00:00Z').getTime()
 const spy=vi.spyOn(Date,'now').mockReturnValue(now)
 try{
  const cases=[
   [0,'Just now','刚刚'],[44,'Just now','刚刚'],[45,'Just now','刚刚'],[59,'Just now','刚刚'],
   [60,'1 minute ago','1 分钟前'],[119,'1 minute ago','1 分钟前'],[120,'2 minutes ago','2 分钟前'],
   [3599,'59 minutes ago','59 分钟前'],[3600,'1 hour ago','1 小时前'],[7200,'2 hours ago','2 小时前'],
   [86399,'23 hours ago','23 小时前'],[86400,'1 day ago','1 天前'],[172800,'2 days ago','2 天前'],
  ] as const
  for(const locale of ['en','zh-CN','en'] as const){
   setLocale(locale)
   for(const [seconds,english,chinese] of cases)expect(formatRelativeTime(now-seconds*1000)).toBe(locale==='en'?english:chinese)
   expect(formatRelativeTime('damaged timestamp')).toBe('—')
   const future=new Date(now+1000)
   expect(formatRelativeTime(future.getTime())).toBe(future.toLocaleString(locale))
   expect(formatRelativeTime(now-7*86400000)).toBe(new Date(now-7*86400000).toLocaleDateString(locale,{month:'short',day:'numeric'}))
  }
 }finally{spy.mockRestore()}
})

afterEach(()=>setLocale('zh-CN'))

it('shares elapsed duration grammar across run history and generation through language switches', () => {
 for (const locale of ['en','zh-CN','en'] as const) {
  setLocale(locale)
  for (const [ms, en, zh] of [[0,'0 sec','0 秒'],[999,'0 sec','0 秒'],[59999,'59 sec','59 秒'],[60000,'1 min 0 sec','1 分钟 0 秒'],[140000,'2 min 20 sec','2 分钟 20 秒'],[1303000,'21 min 43 sec','21 分钟 43 秒'],[3600000,'1 hr 0 min 0 sec','1 小时 0 分钟 0 秒'],[3661999,'1 hr 1 min 1 sec','1 小时 1 分钟 1 秒']] as const) {
   const expected = locale === 'en' ? en : zh
   expect(formatElapsedDuration(ms)).toBe(expected)
   expect(secondsLabel(ms)).toBe(expected)
   if (ms >= 1000) expect(runDuration({id:'r',session_id:'s',text:'original',status:'completed',created_at:1,started_at:100,finished_at:100+ms/1000})).toBe(expected)
  }
  for(const invalid of [-1,NaN,Infinity]) expect(formatElapsedDuration(invalid)).toBeNull()
  expect(runDuration({id:'r',session_id:'s',text:'original',status:'completed',created_at:1})).toBeNull()
 }
})
