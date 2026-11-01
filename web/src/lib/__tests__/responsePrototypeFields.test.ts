import {afterEach,expect,it,vi} from 'vitest'
import {request} from '../http'
import {setLocale,t} from '@/i18n'
import {noticeText} from '../noticeText'

afterEach(()=>{vi.unstubAllGlobals();setLocale('zh-CN')})
const run={id:'run',session_id:'owner',text:'Original text 原文',status:'completed',created_at:1}
const team={team:'fixture',revision:'version',selection_revision:0,recipients:['worker'],recipient_identities:{worker:'identity'}}
function terminal(output_error:unknown) {
  return {backends:['bash'],default_type:'bash',enabled:true,managed_runtime:false,terminals:[{
    sessionId:'terminal',type:'bash',cwd:'/Original 原文',pid:1,status:{kind:'running'},output_error,
  }]}
}

it('rejects prototype-named extra fields in strict responses without repeating a write',async()=>{
  const fetch=vi.fn<typeof globalThis.fetch>();vi.stubGlobal('fetch',fetch)
  for(const locale of ['en','zh-CN','en'] as const) {
    setLocale(locale)
    for(const name of ['constructor','__proto__','toString','hasOwnProperty']) {
      const error={key:'ui.terminal.outputFailed',params:{error:'Original diagnostic 原文'}}
      for(const body of [terminal({...error,[name]:'extra'}),terminal({...error,params:{...error.params,[name]:'extra'}})]) {
        fetch.mockResolvedValueOnce(new Response(JSON.stringify({terminal:body.terminals[0]})))
        const count=fetch.mock.calls.length
        const result=await request('/api/v1/sessions/owner/terminals',{method:'POST',body:'{}'})
        expect(result).toMatchObject({ok:false,code:'response/invalid-schema',status:200})
        if(!result.ok)expect(noticeText(result.copy!)).toBe(t('ui.http.invalidResponse'))
        expect(fetch).toHaveBeenCalledTimes(count+1)
      }
      fetch.mockResolvedValueOnce(new Response(JSON.stringify({...run,team_route:{...team,[name]:'extra'}})))
      expect(await request('/api/v1/sessions/owner/runs/run')).toMatchObject({ok:false,code:'response/invalid-schema'})
    }
  }
})

it('accepts declared data and arbitrary string-map keys without changing original payloads',async()=>{
  const fetch=vi.fn<typeof globalThis.fetch>();vi.stubGlobal('fetch',fetch)
  const output=terminal({key:'ui.terminal.outputFailed',params:{error:'Original diagnostic 原文'}})
  fetch.mockResolvedValueOnce(new Response(JSON.stringify(output)))
  expect(await request('/api/v1/sessions/owner/terminals')).toEqual({ok:true,data:output})
  const created={terminal:output.terminals[0]}
  fetch.mockResolvedValueOnce(new Response(JSON.stringify(created)))
  expect(await request('/api/v1/sessions/owner/terminals',{method:'POST',body:'{}'})).toEqual({ok:true,data:created})
  for(const name of ['constructor','__proto__','toString','hasOwnProperty']) {
    const body={...run,team_route:{...team,recipient_identities:{[name]:'Original identity 原文'}}}
    fetch.mockResolvedValueOnce(new Response(JSON.stringify(body)))
    expect(await request('/api/v1/sessions/owner/runs/run')).toEqual({ok:true,data:body})
    fetch.mockResolvedValueOnce(new Response(JSON.stringify({...body,team_route:{...body.team_route,recipient_identities:{[name]:7}}})))
    expect(await request('/api/v1/sessions/owner/runs/run')).toMatchObject({ok:false,code:'response/invalid-schema'})
  }
  expect(fetch).toHaveBeenCalledTimes(10)
})
