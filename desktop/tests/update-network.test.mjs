import assert from 'node:assert/strict'
import {test} from 'node:test'
import {mkdtemp,rm,readFile} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {createHash} from 'node:crypto'
import {createUpdateFetch, updateNetworkKind, UpdateNetworkFailure, networkSettingsURL} from '../update-network.mjs'
import {InstallerUpdates} from '../installer-updates.mjs'
import {downloadInstaller} from '../mac-installer.mjs'
import {updateFailureOptions} from '../update-dialogs.mjs'
import {setupCopy} from '../setup-copy.mjs'

const url='https://api.github.com/repos/owner/releases/releases/latest'
const dns=()=>Error('net::ERR_NAME_NOT_RESOLVED')
test('metadata and installer share the Chromium session with cookies disabled and original timeout and redirect policy',async t=>{
  const directory=await mkdtemp(path.join(tmpdir(),'harness-update-network-'));t.after(()=>rm(directory,{recursive:true,force:true}))
  const bytes=Buffer.from('installer bytes'),asset={name:'Harness-1.1.0-mac-arm64.dmg',state:'uploaded',size:bytes.length,digest:'sha256:'+createHash('sha256').update(bytes).digest('hex'),browser_download_url:'https://github.com/owner/releases/releases/download/v1.1.0/Harness-1.1.0-mac-arm64.dmg'}
  const calls=[]
  const fetch=createUpdateFetch({fetch:async(request,options)=>{
    calls.push({request,options})
    return request===url?Response.json({tag_name:'v1.1.0',draft:false,prerelease:false,assets:[asset]}):new Response(bytes)
  },resolveProxy:assert.fail},{directFetch:assert.fail})
  const controller=new AbortController(),signal=controller.signal
  const updates=new InstallerUpdates({source:{provider:'github',owner:'owner',repo:'releases'},version:'1.0.0',platform:'darwin',arch:'arm64',packaged:true,fetch,installer:{prepare:async(asset,_version,progress)=>downloadInstaller({fetch,asset,directory,signal,progress})}})
  assert.equal((await updates.check()).state,'available')
  await updates.download()
  assert.equal(updates.state,'downloaded')
  assert.deepEqual(await readFile(path.join(directory,'installer.dmg')),bytes)
  assert.equal(calls.length,2)
  assert.equal(calls[0].options.redirect,'error');assert.equal(calls[1].options.redirect,'follow')
  assert.equal(calls[1].options.signal.aborted,false)
  controller.abort()
  assert.equal(calls[1].options.signal.aborted,true)
  for(const {options} of calls){assert.equal(options.credentials,'omit');assert.equal(options.cache,'no-store')}
})
test('a Chromium DNS failure can use one Node fallback only when the system explicitly reports DIRECT',async()=>{
  const original=dns(),calls=[]
  const signal=new AbortController().signal
  const fetch=createUpdateFetch({fetch:async()=>{calls.push('chromium');throw original},resolveProxy:async request=>{assert.equal(request,url);calls.push('route');return 'DIRECT'}},{directFetch:async(request,options)=>{calls.push('node');assert.equal(request,url);assert.equal(options.signal,signal);return Response.json({version:'1.0.0'})}})
  assert.equal((await fetch(url,{signal})).status,200)
  assert.deepEqual(calls,['chromium','route','node'])
})
test('configured or unresolved proxies are never bypassed, and non-GET requests never fall back',async()=>{
  for(const route of ['PROXY localhost:1234','PROXY localhost:1234; DIRECT','SOCKS5 localhost:1234',null]) {
    const original=dns()
    const fetch=createUpdateFetch({fetch:async()=>{throw original},resolveProxy:async()=>route},{directFetch:assert.fail})
    await assert.rejects(fetch(url),error=>error.menuKey==='updateNetworkDns'&&error.cause===original&&error.params.host==='api.github.com')
  }
  const fetch=createUpdateFetch({fetch:async()=>{throw dns()},resolveProxy:assert.fail},{directFetch:assert.fail})
  await assert.rejects(fetch(url,{method:'POST'}),error=>error.menuKey==='updateNetworkDns')
})
test('HTTP rejection, TLS failures and broken response bodies do not replay transfers',async()=>{
  let requests=0
  const session={fetch:async()=>{requests++;return new Response('',{status:403})},resolveProxy:assert.fail}
  const fetch=createUpdateFetch(session,{directFetch:assert.fail})
  assert.equal((await fetch(url)).status,403);assert.equal(requests,1)
  session.fetch=async()=>{throw Error('net::ERR_CERT_AUTHORITY_INVALID')}
  await assert.rejects(fetch(url),error=>error.menuKey==='updateNetworkTls')
  const original=Error('net::ERR_CONNECTION_RESET')
  session.fetch=async()=>new Response(new ReadableStream({start(controller){controller.error(original)}}))
  await assert.rejects((await fetch(url)).text(),error=>error===original)
})
test('fallback failure keeps both diagnostics and reports the final network category, with no loop',async()=>{
  const original=dns(),final=Object.assign(Error('certificate expired'),{code:'CERT_HAS_EXPIRED'});let count=0
  const fetch=createUpdateFetch({fetch:async()=>{throw original},resolveProxy:async()=> 'DIRECT'},{directFetch:async()=>{count++;throw final}})
  await assert.rejects(fetch(url),error=>error.menuKey==='updateNetworkTls'&&error.cause.errors[0]===original&&error.cause.errors[1]===final)
  assert.equal(count,1)
})
test('cancellation prevents a late DNS failure from starting the alternate transport',async()=>{
  const controller=new AbortController()
  const fetch=createUpdateFetch({fetch:async()=>{controller.abort();throw dns()},resolveProxy:assert.fail},{directFetch:assert.fail})
  await assert.rejects(fetch(url,{signal:controller.signal}),error=>error===controller.signal.reason)
})
test('recognized Node and Chromium codes remain distinct from unknown programming and numeric-code failures',()=>{
  for(const [error,kind] of [[dns(),'Dns'],[Object.assign(Error('lookup failed'),{code:'EAI_AGAIN'}),'Dns'],[Error('net::ERR_PROXY_CONNECTION_FAILED'),'Proxy'],[Error('net::ERR_INTERNET_DISCONNECTED'),'Offline'],[Object.assign(Error('timeout'),{name:'TimeoutError'}),'Timeout'],[Error('net::ERR_CONNECTION_RESET'),'Connection']]) assert.equal(updateNetworkKind(error),kind)
  assert.equal(updateNetworkKind(Error('programming bug')),null)
  assert.equal(updateNetworkKind(Object.assign(Error('unknown'),{code:123})),null)
})
test('same DNS failure renders actionable English Chinese English dialogs with original host and safe action defaults',()=>{
  const error=new UpdateNetworkFailure(dns(),url),shown=[]
  const keys=['updateNetworkDns','updateNetworkDetail','updateRetry','updateNetworkSettings','openReleasePage','ok','updateFailed','updateIntegrityFailed']
  for(const language of ['en','zh-CN','en']) {
    const copy=setupCopy(language),text=Object.fromEntries(keys.map(key=>[key,copy('ui.desktop.menu.'+key)]))
    assert.ok(Object.values(text).every(value=>value.length<=120))
    const failure=updateFailureOptions(error,text,{repository:{provider:'github'},networkSettings:true})
    assert.equal(failure.options.message,text.updateNetworkDns)
    assert.ok(failure.options.detail.includes('api.github.com'));assert.ok(failure.options.detail.includes('ERR_NAME_NOT_RESOLVED'))
    assert.deepEqual(failure.actions,['retry','networkSettings','releases','close'])
    assert.equal(failure.options.defaultId,3);assert.equal(failure.options.cancelId,3)
    assert.equal(failure.options.buttons[0],text.updateRetry)
    shown.push(failure.options.message)
    assert.deepEqual(updateFailureOptions({menuKey:'updateIntegrityFailed',message:'bad digest'},text,{networkSettings:true}).actions,['close'])
  }
  assert.equal(shown[0],shown[2]);assert.notEqual(shown[0],shown[1])
  assert.equal(networkSettingsURL('darwin'),'x-apple.systempreferences:com.apple.preference.network')
  assert.equal(networkSettingsURL('win32'),'ms-settings:network-status');assert.equal(networkSettingsURL('linux'),null)
})

test('real Electron update session reaches a controlled proxy without resolving the origin or using Node fallback',async t=>{
  const {createServer}=await import('node:http'),{execFile}=await import('node:child_process'),{promisify}=await import('node:util'),{createRequire}=await import('node:module'),{fileURLToPath}=await import('node:url')
  const profile=await mkdtemp(path.join(tmpdir(),'harness-update-proxy-'))
  const requests=[]
  const server=createServer((request,response)=>{requests.push(request.url);response.setHeader('Content-Type','application/json');response.end(JSON.stringify({version:'test-proxy'}))})
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve))
  const env={...process.env};delete env.ELECTRON_RUN_AS_NODE
  try {
    const result=await promisify(execFile)(createRequire(import.meta.url)('electron'),[fileURLToPath(new URL('./fixtures/update-network-app.mjs',import.meta.url)),profile,String(server.address().port)],{env,timeout:15000,maxBuffer:1024*1024})
    assert.match(result.stdout,/update-proxy-session-passed/)
    assert.deepEqual(requests,['http://harness-updates.invalid/latest'])
  } finally {
    server.closeAllConnections()
    await new Promise((resolve,reject)=>server.close(error=>error?reject(error):resolve()))
    await rm(profile,{recursive:true,force:true})
  }
})

test('cancellation during a pending proxy lookup promptly ends waiting and late DIRECT cannot start a fallback',async()=>{
  const controller=new AbortController();let route,started
  const lookupStarted=new Promise(resolve=>{started=resolve})
  const fetch=createUpdateFetch({fetch:async()=>{throw dns()},resolveProxy:()=>{started();return new Promise(resolve=>{route=resolve})}},{directFetch:assert.fail})
  const pending=fetch(url,{signal:controller.signal})
  await lookupStarted
  controller.abort()
  await assert.rejects(pending,error=>error===controller.signal.reason)
  route('DIRECT')
  await new Promise(resolve=>setImmediate(resolve))
})
