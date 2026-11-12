import {afterEach,expect,test} from 'vitest'
import {fileReferenceCandidates} from '../fileReferences'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {setLocale,t} from '@/i18n'
const item={path:'Original 文件.txt',absolute_path:'/root/Original 文件.txt',kind:'file'}
const valid={session_id:'owner',query:'query',root:'/root',matches:[item],has_more:false,truncated:false,source:'filesystem'}
afterEach(()=>setLocale('zh-CN'))
for(const [value,key] of [[null,'8f61881b9f28'],[{...valid,session_id:'other'},'4afa7f88ef21'],[{...valid,matches:[{...item,path:'../escape'}]},'0df889a6d933'],[{...valid,matches:[item,item]},'0937e6c4b745']] as const){
 test('file search rejects '+key+' with stable language identity',()=>{
  let error:unknown
  try{fileReferenceCandidates(value,'owner','query','/root')}catch(failure){error=failure}
  expect(error).toBeInstanceOf(Error)
  const copy=failureText(error)
  for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(copy)).toBe(t('ui.lib.fileReferences.'+key))}
 })
}
test('valid references preserve user paths and quoted insertion text',()=>{
 const {candidates}=fileReferenceCandidates(valid,'owner','query','/root')
 expect(candidates[0]).toMatchObject({title:item.path,hint:item.path,insert:JSON.stringify(item.absolute_path)})
 expect(item.path).toBe('Original 文件.txt')
})
