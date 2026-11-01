import {afterEach,expect,test} from 'vitest'
import {setLocale,t} from '@/i18n'
import {environmentReceiptConfirms,importEnvironment,type EnvironmentReceipt} from '../environmentImport'
import {ResourceError,failureText} from '../resourceError'
import {noticeText} from '../noticeText'
afterEach(()=>setLocale('zh-CN'))
const fingerprint='a'.repeat(64)
const receipt:EnvironmentReceipt={receipt_id:'receipt',worktree_id:'worktree',fingerprint,status:'ready',planned_count:1,copied_count:1,skipped_count:0,reused:false,truncated:false,entries:[]}
test('only matching terminal receipts confirm the pending environment write',()=>{
 expect(environmentReceiptConfirms(fingerprint,receipt,'worktree')).toBe(true)
 expect(environmentReceiptConfirms(fingerprint,{...receipt,status:'failed'},'worktree')).toBe(true)
 for(const candidate of [null,{...receipt,worktree_id:'foreign'},{...receipt,fingerprint:'b'.repeat(64)},{...receipt,status:'preparing' as const}])expect(environmentReceiptConfirms(fingerprint,candidate,'worktree')).toBe(false)
 expect(environmentReceiptConfirms(null,receipt,'worktree')).toBe(false)
})
test('invalid selection is rejected before transport and keeps its resource across language changes',async()=>{
 setLocale('en')
 const error=await importEnvironment('fixture','worktree','invalid').catch(error=>error)
 expect(error).toBeInstanceOf(ResourceError)
 const copy=failureText(error)
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(copy)).toBe(t('ui.environment.selectionRequired'))}
})
