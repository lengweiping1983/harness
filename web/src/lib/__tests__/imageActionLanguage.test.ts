import {afterEach,expect,it,vi} from 'vitest'
import {copyImage,copyPng,saveImageAs} from '../imageActions'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {setLocale,t} from '@/i18n'
afterEach(()=>{vi.unstubAllGlobals();setLocale('zh-CN')})
async function resource(operation:()=>Promise<void>,key:string,params?:Record<string,number>){
 let saved:unknown
 try{await operation()}catch(error){saved=error}
 expect(saved).toBeInstanceOf(Error)
 const copy=failureText(saved)
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(copy)).toBe(t(key,params))}
}
it('unsupported clipboard produces an explicit resource before fetching',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);vi.stubGlobal('navigator',{})
 await resource(()=>copyImage('/private.png'),'ui.imageActions.clipboardUnavailable')
 await resource(()=>copyPng(new Blob()),'ui.imageActions.clipboardUnavailable')
 expect(fetch).not.toHaveBeenCalled()
})
it('HTTP image errors retain their status across languages',async()=>{
 const fetch=vi.fn().mockResolvedValue({ok:false,status:503});vi.stubGlobal('fetch',fetch);vi.stubGlobal('window',{})
 await resource(()=>saveImageAs('/private.png','User 原文.png'),'ui.imageActions.httpError',{status:503})
 expect(fetch).toHaveBeenCalledTimes(1)
})
it.each([false,true])('conversion failure closes the bitmap (canvas available: %s)',async available=>{
 const close=vi.fn(),fetch=vi.fn().mockResolvedValue({ok:true,blob:async()=>new Blob(['fixture'],{type:'image/jpeg'})})
 vi.stubGlobal('fetch',fetch);vi.stubGlobal('createImageBitmap',async()=>({width:1,height:1,close}))
 vi.stubGlobal('document',{documentElement:{},createElement:()=>({getContext:()=>available?{drawImage:vi.fn()}:null,toBlob:(callback:(blob:Blob|null)=>void)=>callback(null)})})
 vi.stubGlobal('ClipboardItem',class{constructor(readonly items:Record<string,Promise<Blob>>){}})
 vi.stubGlobal('navigator',{clipboard:{write:async(items:Array<{items:Record<string,Promise<Blob>>}>)=>{await items[0]!.items['image/png']}}})
 await resource(()=>copyImage('/private.jpg'),available?'ui.imageActions.pngFailed':'ui.imageActions.canvasUnavailable')
 expect(close).toHaveBeenCalledTimes(1);expect(fetch).toHaveBeenCalledTimes(1)
})
it.each([false,true])('clipboard denial gives actionable guidance and keeps resource identity (desktop: %s)',async desktop=>{
 const original=new DOMException('Permission denied','NotAllowedError'),write=vi.fn().mockRejectedValue(original)
 vi.stubGlobal('window',desktop?{harnessDesktop:{platform:'darwin'}}:{})
 vi.stubGlobal('navigator',{clipboard:{write}})
 vi.stubGlobal('ClipboardItem',class{constructor(readonly items:Record<string,Blob>){}})
 let saved:unknown
 try{await copyPng(new Blob(['png'],{type:'image/png'}))}catch(error){saved=error}
 expect((saved as Error&{cause:unknown}).cause).toBe(original)
 const copy=failureText(saved),key=desktop?'ui.imageActions.clipboardDeniedDesktop':'ui.imageActions.clipboardDeniedBrowser'
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(copy)).toBe(t(key))}
 expect(write).toHaveBeenCalledTimes(1)
})
it('a non-permission clipboard failure does not send users to permission settings or retry the write',async()=>{
 const original=new Error('clipboard disconnected'),write=vi.fn().mockRejectedValue(original)
 vi.stubGlobal('navigator',{clipboard:{write}})
 vi.stubGlobal('ClipboardItem',class{})
 await resource(()=>copyPng(new Blob()),'ui.imageActions.copyFailed')
 expect(write).toHaveBeenCalledTimes(1)
})
it('copy requests write while PNG encoding is pending and observes the original single operation',async()=>{
 let finish!:(blob:Blob)=>void
 const pending=new Promise<Blob>(resolve=>{finish=resolve})
 const write=vi.fn(async(items:Array<{items:Record<string,Promise<Blob>>}>)=>{await items[0]!.items['image/png']})
 vi.stubGlobal('navigator',{clipboard:{write}})
 vi.stubGlobal('ClipboardItem',class{constructor(readonly items:Record<string,Promise<Blob>>){}})
 const copied=copyPng(pending)
 expect(write).toHaveBeenCalledTimes(1)
 finish(new Blob(['png'],{type:'image/png'}))
 await copied
 expect(write).toHaveBeenCalledTimes(1)
})
it('permission denial also observes a late encoding failure without replaying a write',async()=>{
 let fail!:(error:Error)=>void
 const image=new Promise<Blob>((_resolve,reject)=>{fail=reject})
 const write=vi.fn().mockRejectedValue(new DOMException('denied','NotAllowedError'))
 vi.stubGlobal('window',{harnessDesktop:{platform:'darwin'}})
 vi.stubGlobal('navigator',{clipboard:{write}})
 vi.stubGlobal('ClipboardItem',class{})
 await resource(()=>copyPng(image),'ui.imageActions.clipboardDeniedDesktop')
 fail(new Error('encoding failed later'))
 await new Promise(resolve=>setImmediate(resolve))
 expect(write).toHaveBeenCalledTimes(1)
})
