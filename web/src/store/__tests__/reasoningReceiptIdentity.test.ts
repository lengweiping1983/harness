import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {api,type ApiFailure} from '@/lib/api'
import {setLocale,t} from '@/i18n'
import {noticeText} from '@/lib/noticeText'
import type {SessionDetail} from '@/types'
import {useAppStore} from '../app'
const unknown:ApiFailure={ok:false,status:0,code:'network',message:'Original network',copy:{key:'ui.http.invalidResponse'}}
const detail=(id:string,effort:string):SessionDetail=>({header:{id,metadata:{reasoning_effort:effort}},events:[],messages:[],usage:{},stats:{},todos:[],timeline:[]})
beforeEach(()=>{setLocale('en');useAppStore.setState({activeSessionId:'owner',sessions:[{id:'owner',reasoning_effort:'low',metadata:{reasoning_effort:'low'}},{id:'other',reasoning_effort:'low'}],reasoningSaves:{}})})
afterEach(()=>{vi.restoreAllMocks();setLocale('zh-CN')})
it.each(['other-id','wrong-effort'])('mismatched %s write receipt requires one read without replay',async(kind)=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue({ok:true,data:{session:{id:kind==='other-id'?'other':'owner',metadata:{reasoning_effort:kind==='wrong-effort'?'low':'high'}}}})
 const read=vi.spyOn(api,'getSession').mockResolvedValue(unknown)
 await useAppStore.getState().setReasoningEffort('high')
 expect(read).toHaveBeenCalledExactlyOnceWith('owner','conversation-window')
 expect(write).toHaveBeenCalledTimes(1)
 expect(useAppStore.getState().reasoningSaves.owner.unconfirmed).toBe(true)
 expect(useAppStore.getState().sessions[1].reasoning_effort).toBe('low')
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(useAppStore.getState().reasoningSaves.owner.error!)).toBe(t('ui.http.invalidResponse'))}
})
it('another conversation cannot confirm a lost reasoning write',async()=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue(unknown)
 vi.spyOn(api,'getSession').mockResolvedValue({ok:true,data:detail('other','medium')})
 await useAppStore.getState().setReasoningEffort('high')
 expect(useAppStore.getState().reasoningSaves.owner.unconfirmed).toBe(true)
 expect(useAppStore.getState().sessions[0].reasoning_effort).toBe('high')
 expect(useAppStore.getState().sessions[1].reasoning_effort).toBe('low')
 expect(write).toHaveBeenCalledTimes(1)
})
it('an authoritative read of the original conversation restores its actual setting',async()=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue(unknown)
 vi.spyOn(api,'getSession').mockResolvedValue({ok:true,data:detail('owner','medium')})
 await useAppStore.getState().setReasoningEffort('high')
 expect(useAppStore.getState().sessions[0].reasoning_effort).toBe('medium')
 expect(useAppStore.getState().reasoningSaves.owner.unconfirmed).toBe(false)
 expect(write).toHaveBeenCalledTimes(1)
})
it.each([['high',false], [null,false], [null,true]] as const)('matching %s inherit %s completes without a read',async(effort,inherit)=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue({ok:true,data:{session:{id:'owner',metadata:inherit?{}:{reasoning_effort:effort??''}}}})
 const read=vi.spyOn(api,'getSession')
 await useAppStore.getState().setReasoningEffort(effort,inherit)
 expect(useAppStore.getState().reasoningSaves.owner).toMatchObject({pending:false,error:null,unconfirmed:false})
 expect(useAppStore.getState().sessions[0].reasoning_effort).toBe(effort)
 expect(read).not.toHaveBeenCalled();expect(write).toHaveBeenCalledTimes(1)
})
