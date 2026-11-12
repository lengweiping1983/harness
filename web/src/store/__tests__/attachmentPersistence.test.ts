import {afterEach,expect,it,vi} from 'vitest'
import {useDraftStore} from '../drafts'
import {setLocale,t} from '@/i18n'
import {noticeText} from '@/lib/noticeText'
import type {DraftAttachment} from '@/lib/attachments'
afterEach(()=>{vi.unstubAllGlobals();setLocale('zh-CN')})
it('round-trips resource failures and external diagnostics while marking incomplete uploads without replay',async()=>{
 const base={name:'Original 原文.txt',mediaType:'text/plain',size:4,isImage:false}
 const attachments:DraftAttachment[]=[
  {...base,localId:'pending',status:'uploading',previewUrl:'blob:pending'},
  {...base,localId:'resource',status:'error',error:{key:'ui.http.invalidResponse'}},
  {...base,localId:'external',status:'error',error:'Provider 原文'},
  {...base,localId:'ready',attachmentId:'a'.repeat(64)},
 ]
 useDraftStore.getState().updateAttachments('fixture',()=>attachments)
 const partial=useDraftStore.persist.getOptions().partialize!(useDraftStore.getState())
 const serialized=JSON.stringify({state:partial,version:0})
 expect(serialized).not.toContain('blob:pending')
 const stored=JSON.parse(serialized).state.bySession.fixture.attachments
 expect(stored[0].error).toEqual({key:'ui.store.drafts.5dc58adf4cac'})
 expect(stored[1].error).toEqual({key:'ui.http.invalidResponse'})
 expect(stored[2].error).toBe('Provider 原文');expect(stored[3].status).toBeUndefined()
 vi.stubGlobal('localStorage',{getItem:()=>serialized,setItem:vi.fn()})
 await useDraftStore.persist.rehydrate()
 const restored=useDraftStore.getState().bySession.fixture.attachments
 for(const locale of ['en','zh-CN','en'] as const){
  setLocale(locale)
  expect(noticeText(restored[0]!.error??'')).toBe(t('ui.store.drafts.5dc58adf4cac'))
  expect(noticeText(restored[1]!.error??'')).toBe(t('ui.http.invalidResponse'))
  expect(noticeText(restored[2]!.error??'')).toBe('Provider 原文')
 }
 expect(restored.map(row=>row.name)).toEqual(attachments.map(row=>row.name))
})
