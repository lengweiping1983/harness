import assert from 'node:assert/strict'
import test from 'node:test'
import {api} from '../../extensions/chatgpt-companion/client.mjs'

const settings={endpoint:'http://127.0.0.1:8877/',token:'private-fixture-token'}

test('missing token and non-loopback address never start a request',async()=>{
 let calls=0
 globalThis.fetch=async()=>{calls++;throw Error('must not fetch')}
 await assert.rejects(api({...settings,token:''},'/health'),{message:'pairing_required'})
 await assert.rejects(api({...settings,endpoint:'https://example.com/'},'/health'),{message:'invalid_endpoint'})
 assert.equal(calls,0)
})

test('pairing refusal, service refusal, malformed response and transport failure stay distinct',async()=>{
 for(const [response,expected] of [
  [{ok:false,status:403},'pairing_denied'],
  [{ok:false,status:409},'bridge_request_failed'],
  [{ok:true,json:async()=>{throw SyntaxError('bad JSON')}},'bridge_response_invalid'],
  [{ok:true,json:async()=>[]},'bridge_response_invalid'],
  [null,'bridge_unavailable'],
 ]){
  let calls=0
  globalThis.fetch=async()=>{calls++;if(!response)throw TypeError('network');return response}
  await assert.rejects(api(settings,'/bind',{session_id:'fixture'}),{message:expected})
  assert.equal(calls,1)
 }
})

test('authenticated reads keep the token in a header and never follow redirects',async()=>{
 globalThis.fetch=async(url,options)=>{
  assert.equal(url,'http://127.0.0.1:8877/health')
  assert.equal(options.method,'GET')
  assert.equal(options.headers.Authorization,'Bearer '+settings.token)
  assert.equal(options.redirect,'error')
  assert.equal(options.body,undefined)
  return {ok:true,json:async()=>({executor:'ready'})}
 }
 assert.deepEqual(await api(settings,'/health'),{executor:'ready'})
})
