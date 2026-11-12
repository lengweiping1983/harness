import {afterEach,expect,it} from 'vitest'
import {admitFile,MAX_ATTACHMENT_BYTES,formatSize} from '../attachments'
import {setLocale,t} from '@/i18n'
import {noticeText} from '../noticeText'
afterEach(()=>setLocale('zh-CN'))
it.each([
 ['empty.txt',new Uint8Array(),0,'ui.lib.attachments.1bdb05a4e82b',{}],
 ['large.txt',new Uint8Array([97]),MAX_ATTACHMENT_BYTES+1,'ui.lib.attachments.35cd8a7fd9be',{p0:formatSize(MAX_ATTACHMENT_BYTES+1),p1:formatSize(MAX_ATTACHMENT_BYTES)}],
 ['Original 原文.exe',new Uint8Array([97]),1,'ui.lib.attachments.40adad246a3f',{p0:'exe'}],
 ['unknown.abc',new Uint8Array([97]),1,'ui.lib.attachments.40adad246a3f',{p0:'abc'}],
 ['Original 原文',new Uint8Array([0,1]),2,'ui.lib.attachments.f25703aa90d5',{p0:'Original 原文'}],
 ['image.jpg',new Uint8Array([0x89,0x50,0x4e,0x47,0,0,0,0]),8,'ui.lib.attachments.f6703267b814',{p0:'image/png',p1:'jpg'}],
] as const)('keeps %s rejection identity and arguments across languages',(name,bytes,size,key,params)=>{
 setLocale('en');const result=admitFile(name,bytes,size)
 expect(result.ok).toBe(false);expect(result.message?.key).toBe(key)
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(result.message??'')).toBe(t(key,params))}
})
