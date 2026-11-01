import {afterEach,expect,test} from 'vitest'
import {setLocale,t} from '@/i18n'
import {ApiError} from '../http'
import type { ResourceText } from '@/types'
import {ResourceError,failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {editHookForm,readHookForm,requireHookGroup,removeHookCommand} from '../hookForm'
import {addHookTemplate} from '../projectHooks'
afterEach(()=>setLocale('zh-CN'))
test('unknown writes resolve both the outer notice and its transport reason in the current language',()=>{
 setLocale('en')
 const saved: ResourceText={template:{key:'ui.extensions.unknownResult',params:{reason:failureText(new ApiError({ok:false,status:0,code:'network',message:'Old snapshot',copy:{key:'ui.lib.api.4a5f658ed9ae',params:{detail:'Original diagnostic 原文'}}}))}}}
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(saved)).toBe(t('ui.extensions.unknownResult',{reason:t('ui.lib.api.4a5f658ed9ae',{detail:'Original diagnostic 原文'})}))}
})
test('unknown writes preserve external causes without reverse translation',()=>{
 const saved: ResourceText={template:{key:'ui.extensions.unknownResult',params:{reason:failureText(new Error('Original refusal 原文'))}}}
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(saved)).toBe(t('ui.extensions.unknownResult',{reason:'Original refusal 原文'}))}
})
function rejection(action:()=>unknown,key:string){
 let saved:unknown
 try{action()}catch(error){saved=error}
 expect(saved).toBeInstanceOf(ResourceError)
 const copy=failureText(saved)
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(copy)).toBe(t(key))}
}
test('invalid Hook JSON is not edited and retains its error resource',()=>{
 let edited=false
 rejection(()=>editHookForm('not json',()=>{edited=true}),'ui.lib.hookForm.5d6f65ba77be')
 expect(edited).toBe(false)
})
test('unsupported Hook fields are rejected without normalizing away the original text',()=>{
 const text='{"hooks":{},"unknown":"Original 原文"}'
 rejection(()=>editHookForm(text,()=>{throw Error('Must not edit')}),'ui.lib.hookForm.870f3566c2ba')
 const form=readHookForm(text);expect(form.document).toBeNull();expect(typeof form.error).toBe('object')
})
test('missing Hook groups and commands preserve current identities',()=>{
 rejection(()=>requireHookGroup({hooks:{}},'PreToolUse',0),'ui.lib.hookForm.3b18cb93efff')
 rejection(()=>removeHookCommand({hooks:{PreToolUse:[{hooks:[]}]}},'PreToolUse',0,0),'ui.lib.hookForm.97f0222caa24')
})
test('invalid template JSON and group shapes retain explicit resources',()=>{
 rejection(()=>addHookTemplate('not json',0),'ui.lib.hookForm.5d6f65ba77be')
 rejection(()=>addHookTemplate('{"hooks":[]}',0),'ui.lib.projectHooks.b11f90cf5cc8')
 rejection(()=>addHookTemplate('{"hooks":{"PreToolUse":{}}}',0),'ui.lib.projectHooks.275e2edd9b13')
})
test('valid Hook edits preserve command, status and matcher originals',()=>{
 const text='{"hooks":{"PreToolUse":[{"matcher":"OriginalTool","hooks":[{"type":"command","command":"echo 原文","statusMessage":"Original status 原文"}]}]}}'
 const form=readHookForm(text);expect(form.error).toBe('')
 const saved=editHookForm(text,document=>{document.enabled=false})
 expect(JSON.parse(saved)).toEqual({...JSON.parse(text),enabled:false})
})
