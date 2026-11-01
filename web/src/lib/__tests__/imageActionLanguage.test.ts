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
