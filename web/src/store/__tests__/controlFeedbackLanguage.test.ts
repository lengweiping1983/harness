import {afterEach, expect, it, vi} from 'vitest'
import {setLocale, t} from '@/i18n'
import {api} from '@/lib/api'
import * as http from '@/lib/http'
import {noticeText} from '@/lib/noticeText'
import {ResourceError} from '@/lib/resourceError'
import {usePlanModeStore} from '../planModes'
import {useStopStore} from '../stops'
import {useChatStore} from '../chat'

afterEach(() => {vi.restoreAllMocks();setLocale('zh-CN');usePlanModeStore.setState({saves:{}});useStopStore.setState({requests:{}});useChatStore.setState({sessions:{}})})
const failure = {ok:false as const,status:0,code:'timeout',message:'Old diagnostic',copy:{key:'ui.http.invalidResponse'}}

it.each(['response','exception','external'] as const)('计划保存保留%s错误身份，切换语言不重复写入', async mode => {
  const write=vi.spyOn(api,'setPlanMode')
  if(mode==='response')write.mockResolvedValue(failure)
  else write.mockRejectedValue(mode==='exception'?new ResourceError(failure.copy):new Error('Original error 原文'))
  const read=vi.spyOn(api,'planMode').mockResolvedValue(failure)
  await usePlanModeStore.getState().change('fixture',true)
  const feedback=usePlanModeStore.getState().saves.fixture!
  expect(feedback.unconfirmed).toBe(true)
  for(const language of ['en','zh-CN','en'] as const){setLocale(language);expect(noticeText(feedback.message!)).toBe(mode==='external'?'Original error 原文':t(failure.copy.key))}
  expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(mode==='response'?1:0)
})

it.each(['unconfirmed','invalid','exception','external'] as const)('停止请求保留%s反馈，切换语言不重放', async mode => {
  const send=vi.spyOn(http,'runtimeRequest')
  if(mode==='unconfirmed')send.mockResolvedValue({requested:false})
  else if(mode==='invalid')send.mockResolvedValue({requested:'invalid'})
  else send.mockRejectedValue(mode==='exception'?new http.ApiError(failure):new Error('Original error 原文'))
  const refresh=vi.fn()
  await useStopStore.getState().stop('fixture',1,refresh)
  const feedback=useStopStore.getState().requests.fixture!
  expect(feedback.phase).toBe('unconfirmed')
  const key=mode==='unconfirmed'?'ui.store.stops.5ad9d939a628':mode==='invalid'?'ui.store.stops.368d196fe987':failure.copy.key
  for(const language of ['en','zh-CN','en'] as const){setLocale(language);expect(noticeText(feedback.message!)).toBe(mode==='external'?'Original error 原文':t(key))}
  expect(send).toHaveBeenCalledTimes(1);expect(refresh).toHaveBeenCalledTimes(1)
})
