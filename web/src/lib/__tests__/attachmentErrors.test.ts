import {afterEach,expect,it,vi} from 'vitest'
import {attachmentErrorCopy} from '../attachmentErrors'
import {api} from '../api'
import {ApiError} from '../http'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {setLocale,t} from '@/i18n'
afterEach(()=>{vi.unstubAllGlobals();setLocale('zh-CN')})
it.each([
 ['attachment/empty','empty',{}],
 ['attachment/bad-payload','entryObject',{p0:'2'}],
 ['attachment/bad-payload','invalidPayload',{}],
 ['attachment/bad-base64','base64Decode',{p0:'Original decoder 原文'}],
 ['attachment/too-large','fileTooLarge',{p0:'32',p1:'40000'}],
 ['attachment/type-denied','extensionDenied',{p0:'.exe'}],
 ['attachment/type-denied','mediaDenied',{p0:'application/x-executable'}],
 ['attachment/unsupported','unsupported',{p0:'Original 原文.dat'}],
 ['attachment/too-many','tooMany',{p0:20,p1:21}],
] as const)('upload preserves registered %s/%s identity without replay',async(code,suffix,params)=>{
 const key='ui.attachments.server.'+suffix
 const fetch=vi.fn().mockResolvedValue(new Response(JSON.stringify({error:'Original server snapshot 原文',code,details:{copy:{key,params}}}),{status:400}));vi.stubGlobal('fetch',fetch)
 const result=await api.uploadAttachments([{name:'Original 原文.dat',mediaType:'text/plain',data:'YQ=='}])
 expect(result.ok).toBe(false)
 if(result.ok)throw Error('Expected rejection')
 expect(result.status).toBe(400);expect(result.code).toBe(code);expect(result.message).toBe('Original server snapshot 原文')
 const value=failureText(new ApiError(result))
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(value)).toBe(t(key,params))}
 expect(fetch).toHaveBeenCalledTimes(1)
})
it.each([
 undefined,
 {copy:{key:'ui.store.app.495e7952925d'}},
 {copy:{key:'ui.attachments.server.extensionDenied'}},
 {copy:{key:'ui.attachments.server.extensionDenied',params:{p0:true}}},
 {copy:{key:'ui.attachments.server.extensionDenied',params:{p0:'.exe',extra:'unknown'}}},
 {copy:{key:'constructor',params:{p0:'.exe'}}},
 {copy:{key:'ui.attachments.server.extensionDenied',params:{p0:Infinity}}},
 {copy:{key:'ui.attachments.server.extensionDenied',params:[]}},
])('rejects malformed or unrelated copy metadata',details=>{
 expect(attachmentErrorCopy('attachment/type-denied',details)).toBeNull()
})
it('unknown external codes never acquire product resources',()=>{
 expect(attachmentErrorCopy('fixture/external',{copy:{key:'ui.attachments.server.empty'}})).toBeUndefined()
 expect(attachmentErrorCopy('constructor',{})).toBeUndefined()
})
it('preserves written attachment references and an unavailable status without replaying upload',async()=>{
 const details={copy:{key:'ui.attachments.server.statsFailed'},attachments:[{attachmentId:'a'.repeat(64)}]}
 const fetch=vi.fn().mockResolvedValue(new Response(JSON.stringify({error:'Snapshot',code:'attachment/stats-unavailable',details}),{status:503}));vi.stubGlobal('fetch',fetch)
 const result=await api.uploadAttachments([{name:'Original.txt',mediaType:'text/plain',data:'YQ=='}])
 expect(result).toMatchObject({ok:false,status:503,code:'attachment/stats-unavailable',details})
 if(result.ok)throw Error('Expected unavailable statistics')
 const value=failureText(new ApiError(result))
 for(const language of ['en','zh-CN','en'] as const){await setLocale(language);expect(noticeText(value)).toBe(t(details.copy.key))}
 expect(fetch).toHaveBeenCalledTimes(1)
})
it('missing registered metadata is an invalid response, preserving diagnostic and status',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({error:'Original diagnostic',code:'attachment/empty'}),{status:400})))
 const result=await api.uploadAttachments([])
 expect(result).toMatchObject({ok:false,status:400,code:'response/invalid-schema',message:'Original diagnostic',copy:{key:'ui.http.invalidResponse'}})
})
