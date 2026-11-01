import {afterEach,expect,test,vi} from 'vitest'
import {terminalInputQueue} from '../terminalInput'
import {terminalResizeQueue} from '../terminalResize'
import {setLocale,t} from '@/i18n'
import {ApiError} from '../http'
import {ResourceError,failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import type {ResourceText} from '@/types'
afterEach(()=>{vi.useRealTimers();setLocale('zh-CN')})
function language(copy:ResourceText,key:string,params:()=>Record<string,string|number>){for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(copy)).toBe(t(key,params()))}}
test('keyboard failure retains a composed resource and never replays its failed batch',async()=>{
 vi.useFakeTimers()
 const error:ResourceText={template:{key:'ui.components.inspector.TerminalWorkbench.403e9e4e38b1',params:{p0:{key:'ui.http.invalidResponse'}}}}
 const write=vi.fn().mockResolvedValue(error),fail=vi.fn()
 const queue=terminalInputQueue(write,fail)
 queue.push('Original input 原文');await vi.advanceTimersByTimeAsync(16)
 expect(fail).toHaveBeenCalledTimes(1)
 language(fail.mock.calls[0][0],'ui.components.inspector.TerminalWorkbench.403e9e4e38b1',()=>({p0:t('ui.http.invalidResponse')}))
 queue.push('Must remain blocked');await vi.advanceTimersByTimeAsync(1000)
 expect(write).toHaveBeenCalledExactlyOnceWith('Original input 原文')
 queue.dispose()
})
test('keyboard overflow reports its resource before sending any data',()=>{
 vi.useFakeTimers();const write=vi.fn(),fail=vi.fn()
 const queue=terminalInputQueue(write,fail);queue.push('x'.repeat(64001))
 language(fail.mock.calls[0][0],'ui.lib.terminalInput.f77b1174e9f3',()=>({}))
 expect(write).not.toHaveBeenCalled();queue.dispose()
})
test('thrown input transport failure keeps resource identity and its stopped state',async()=>{
 vi.useFakeTimers();const write=vi.fn().mockRejectedValue(new ApiError({ok:false,status:200,code:'response/invalid-json',message:'snapshot',copy:{key:'ui.http.invalidResponse'}})),fail=vi.fn()
 const queue=terminalInputQueue(write,fail);queue.push('input');await vi.advanceTimersByTimeAsync(16)
 language(fail.mock.calls[0][0],'ui.http.invalidResponse',()=>({}));queue.push('blocked');await vi.advanceTimersByTimeAsync(100)
 expect(write).toHaveBeenCalledTimes(1);queue.dispose()
})
test('resize failures retain resources while successful current sizes remain retryable',async()=>{
 vi.useFakeTimers();const write=vi.fn().mockRejectedValueOnce(new ApiError({ok:false,status:200,code:'response/invalid-json',message:'snapshot',copy:{key:'ui.http.invalidResponse'}})).mockResolvedValue(null),report=vi.fn()
 const queue=terminalResizeQueue(write,report);queue.resize(80,24);queue.setActive(true);await vi.advanceTimersByTimeAsync(100)
 language(report.mock.calls[0][0],'ui.http.invalidResponse',()=>({}))
 expect(write).toHaveBeenCalledTimes(1)
 queue.resize(100,30);await vi.advanceTimersByTimeAsync(100)
 expect(write).toHaveBeenLastCalledWith({cols:100,rows:30});expect(report).toHaveBeenLastCalledWith(null);queue.dispose()
})
test('nested resource errors resolve all levels and preserve raw text and numeric zero',()=>{
 const copy:ResourceText={template:{key:'ui.extensions.unknownResult',params:{reason:{template:{key:'ui.components.inspector.TerminalWorkbench.e19d2d690b52',params:{p0:0,p1:{key:'ui.http.invalidResponse'}}}}}}}
 const error=new ResourceError(copy)
 language(failureText(error),'ui.extensions.unknownResult',()=>({reason:t('ui.components.inspector.TerminalWorkbench.e19d2d690b52',{p0:0,p1:t('ui.http.invalidResponse')})}))
 expect(noticeText({template:{key:'ui.extensions.unknownResult',params:{reason:'Original {key} 原文'}}})).toContain('Original {key} 原文')
 expect(()=>noticeText({template:{key:'ui.extensions.unknownResult',params:{reason:{key:'missing.resource'}}}})).toThrow()
})
