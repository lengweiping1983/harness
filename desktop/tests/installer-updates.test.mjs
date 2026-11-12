import assert from 'node:assert/strict'
import {test} from 'node:test'
import {InstallerUpdates} from '../installer-updates.mjs'

const source={provider:'github',owner:'lengweiping1983',repo:'SuperHarness'}
function release(version,assets=true) {
  const name=`Harness-${version}-mac-arm64.dmg`
  return {tag_name:'v'+version,draft:false,prerelease:false,assets:assets?[{name,state:'uploaded',size:123,browser_download_url:`https://github.com/${source.owner}/${source.repo}/releases/download/v${version}/${name}`}]:[]}
}
function fixture(data=release('0.1.3'),options={}) {
  const calls=[]
  const updates=new InstallerUpdates({source,version:'0.1.2',platform:'darwin',arch:'arm64',packaged:true,
    fetch:async(...args)=>{calls.push(args);return Response.json(data)},...options})
  return {updates,calls}
}

test('an unpublished local version or equal release is current without requiring latest-mac.yml or ZIP',async()=>{
  for(const version of ['0.1.1','0.1.2']) {
    const {updates,calls}=fixture(release(version))
    assert.equal((await updates.check()).state,'current')
    assert.equal(calls.length,1)
    assert.equal(calls[0][0],'https://api.github.com/repos/lengweiping1983/SuperHarness/releases/latest')
    assert.equal(updates.downloadUrl,null)
  }
})
test('a newer release exposes only the exact installer for this system and architecture',async()=>{
  const {updates}=fixture()
  const result=await updates.check()
  assert.equal(result.state,'available')
  assert.equal(result.mode,'installer')
  assert.equal(result.downloadUrl,'https://github.com/lengweiping1983/SuperHarness/releases/download/v0.1.3/Harness-0.1.3-mac-arm64.dmg')
  assert.equal(result.progress,null)
})
test('missing release and missing platform installer stay distinct from network failure',async()=>{
  const missing=fixture(null,{fetch:async()=>new Response('',{status:404})}).updates
  assert.equal((await missing.check()).reason,'unpublished')
  assert.equal(missing.state,'unavailable')
  const platform=fixture(release('0.1.3',false)).updates
  assert.equal((await platform.check()).reason,'platform')
  const original=Error('network diagnostic')
  const failed=fixture(null,{fetch:async()=>{throw original}}).updates
  await assert.rejects(failed.check(),error=>error===original)
  assert.equal(failed.state,'error')
})
test('concurrent checks share a single read and development never accesses the network',async()=>{
  let respond, count=0
  const {updates}=fixture(null,{fetch:()=>{count++;return new Promise(resolve=>{respond=resolve})}})
  const first=updates.check()
  assert.equal(updates.check(),first)
  await Promise.resolve()
  respond(Response.json(release('0.1.3')))
  assert.equal((await first).state,'available')
  assert.equal(updates.pending,null)
  const development=fixture(null,{packaged:false})
  assert.equal((await development.updates.check()).state,'development')
  assert.deepEqual(development.calls,[])
  assert.equal(count,1)
})
test('untrusted downloads, malformed versions, draft releases and oversized metadata are rejected',async()=>{
  for(const change of [value=>{value.assets[0].browser_download_url='https://elsewhere.invalid/installer.dmg'},
    value=>{value.tag_name='v1.0.0-beta'},value=>{value.draft=true},value=>{value.assets[0].size=true}]) {
    const value=release('0.1.3');change(value)
    const {updates}=fixture(value)
    await assert.rejects(updates.check())
    assert.equal(updates.state,'error')
    assert.equal(updates.downloadUrl,null)
  }
  await assert.rejects(fixture(null,{fetch:async()=>Response.json({extra:'a'.repeat(1024*1024+1)})}).updates.check(),error=>error.menuKey==='updateMetadataTooLarge')
})

test('managed mac download shares one operation, reports real progress and retains ready update across checks',async()=>{
  let finish,prepares=0
  const installer={prepare:async(asset,version,progress)=>{
    prepares++;assert.equal(asset.size,123);assert.equal(version,'0.1.3');progress(37)
    await new Promise(resolve=>{finish=resolve})
  }}
  const {updates,calls}=fixture(undefined,{installer})
  await updates.check()
  const first=updates.download()
  assert.equal(updates.download(),first)
  assert.equal(updates.check(),first)
  await Promise.resolve()
  assert.equal(updates.progress,37)
  assert.equal(updates.state,'downloading')
  finish();await first
  assert.equal(updates.state,'downloaded')
  assert.equal(updates.progress,100)
  assert.equal((await updates.check()).state,'downloaded')
  assert.equal(prepares,1);assert.equal(calls.length,1)
})

test('managed download failures never report ready and close cancels and joins the original operation',async()=>{
  let fail,cancelled=0,discarded=0
  const original=Error('transfer failed')
  const installer={prepare:()=>new Promise((_resolve,reject)=>{fail=reject}),cancel:()=>{cancelled++;fail(original)},discard:async()=>{discarded++}}
  const {updates}=fixture(undefined,{installer})
  await assert.rejects(updates.download())
  await updates.check()
  const action=updates.download()
  await Promise.resolve()
  const observed=assert.rejects(action,error=>error===original)
  await updates.close();await observed
  assert.equal(cancelled,1);assert.equal(discarded,1)
  assert.equal(updates.state,'error');assert.equal(updates.pending,null)
})
