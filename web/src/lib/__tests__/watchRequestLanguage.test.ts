import {afterEach, expect, test, vi} from 'vitest'
import {setLocale, t} from '@/i18n'
import type {ResourceText} from '@/types'
import {watchRequestStatus} from '../watchRequestStatus'
import {noticeText} from '../noticeText'

afterEach(() => {vi.useRealTimers();setLocale('zh-CN')})
test('poll failure keeps resource identity without language changes triggering reads', async () => {
  vi.useFakeTimers()
  const read = vi.fn(async () => ({ok:false as const,status:403,code:'response/invalid-json',message:'old prose',copy:{key:'ui.http.invalidResponse'}}))
  let saved: ResourceText = ''
  const watch = watchRequestStatus(read,()=>{throw Error('Unexpected result')},value=>{saved=value},'started',{hidden:()=>false,subscribe:()=>()=>{}})
  try {
    await vi.advanceTimersByTimeAsync(3000)
    expect(read).toHaveBeenCalledTimes(1)
    for(const language of ['en','zh-CN','en'] as const){setLocale(language);expect(noticeText(saved)).toBe(t('ui.http.invalidResponse'))}
    await vi.advanceTimersByTimeAsync(60000)
    expect(read).toHaveBeenCalledTimes(1)
  } finally {watch.dispose()}
})
