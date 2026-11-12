import {afterEach,expect,it,vi} from 'vitest'
import {setLocale,t} from '@/i18n'
import {noticeText} from '@/lib/noticeText'
import {useDraftStore} from '../drafts'
afterEach(()=>{vi.unstubAllGlobals();setLocale('zh-CN')})
it('failed browser storage retains the input and a current-language resource without rewriting on locale changes',async()=>{
 setLocale('en')
 const write=vi.fn((_key:string)=>{throw new Error('External storage diagnostic')})
 vi.stubGlobal('localStorage',{setItem:write,getItem:()=>null})
 useDraftStore.getState().setText('fixture','User draft 原文')
 await Promise.resolve()
 const draftWrites=()=>write.mock.calls.filter(([key])=>key==='harness.drafts.v1').length
 const error=useDraftStore.getState().storageError,before=draftWrites()
 expect(error).toEqual({key:'ui.store.drafts.f9e99a9805ac'})
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(error??'')).toBe(t('ui.store.drafts.f9e99a9805ac'));expect(useDraftStore.getState().bySession.fixture.text).toBe('User draft 原文')}
 expect(draftWrites()).toBe(before)
})
